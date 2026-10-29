"""混合召回：稠密向量 + 稀疏关键词，用 RRF 融合。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from qdrant_client.http import models as qm

from app.core.config import settings
from app.rag.index import qdrant_store
from app.rag.index.qdrant_store import DENSE_VECTOR, SPARSE_VECTOR

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    chunk_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    boosted: float = 0.0

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))

    @property
    def source_file(self) -> str:
        return str(self.payload.get("source_file", ""))

    @property
    def section_path_str(self) -> str:
        return str(self.payload.get("section_path_str", ""))


def _build_filter(doc_types: list[str] | None, max_priority: int | None) -> qm.Filter | None:
    must: list[qm.Condition] = []
    if doc_types:
        must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=doc_types)))
    if max_priority is not None:
        must.append(
            qm.FieldCondition(key="source_priority", range=qm.Range(lte=max_priority))
        )
    return qm.Filter(must=must) if must else None


def search_dense(
    vector: list[float],
    *,
    limit: int,
    doc_types: list[str] | None = None,
    max_priority: int | None = None,
    collection: str | None = None,
) -> list[Hit]:
    client = qdrant_store.get_client()
    res = client.query_points(
        collection_name=collection or settings.qdrant_collection,
        query=vector,
        using=DENSE_VECTOR,
        limit=limit,
        query_filter=_build_filter(doc_types, max_priority),
        with_payload=True,
    )
    return [
        Hit(
            chunk_id=str(p.payload.get("chunk_id", "")),
            score=float(p.score),
            payload=p.payload or {},
            dense_score=float(p.score),
        )
        for p in res.points
    ]


def search_sparse(
    indices: list[int],
    values: list[float],
    *,
    limit: int,
    doc_types: list[str] | None = None,
    max_priority: int | None = None,
    collection: str | None = None,
) -> list[Hit]:
    if not indices:
        return []
    client = qdrant_store.get_client()
    res = client.query_points(
        collection_name=collection or settings.qdrant_collection,
        query=qm.SparseVector(indices=indices, values=values),
        using=SPARSE_VECTOR,
        limit=limit,
        query_filter=_build_filter(doc_types, max_priority),
        with_payload=True,
    )
    return [
        Hit(
            chunk_id=str(p.payload.get("chunk_id", "")),
            score=float(p.score),
            payload=p.payload or {},
            sparse_score=float(p.score),
        )
        for p in res.points
    ]


def rrf_fuse(
    result_lists: list[list[Hit]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[Hit]:
    """Reciprocal Rank Fusion。自实现而非用 Qdrant 内置，因为需要保留中间分数做调试。"""
    if weights is None:
        weights = [1.0] * len(result_lists)

    merged: dict[str, Hit] = {}
    scores: dict[str, float] = {}

    for weight, hits in zip(weights, result_lists, strict=False):
        for rank, h in enumerate(hits):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + weight / (k + rank + 1)
            if h.chunk_id not in merged:
                merged[h.chunk_id] = h
            else:
                cur = merged[h.chunk_id]
                cur.dense_score = cur.dense_score or h.dense_score
                cur.sparse_score = cur.sparse_score or h.sparse_score

    out: list[Hit] = []
    for cid, score in scores.items():
        h = merged[cid]
        h.rrf_score = score
        h.score = score
        out.append(h)

    out.sort(key=lambda x: -x.rrf_score)
    return out


def apply_entity_boost(
    hits: list[Hit],
    *,
    formulas: list[str],
    diseases: list[str],
    acupoints: list[str],
    symptoms: list[str] | None = None,
) -> None:
    """实体命中加分。比调 dense/sparse 权重更稳，且可解释。

    注意：病名只做 soft boost，绝不做硬过滤 —— 医案原表标签有错标，
    硬过滤会让正确医案永远检索不到。

    分值处理：**这里是刻意的加法，且加分绝对值刻意大于 RRF 分的量级**。
    RRF 融合后单条列表 rank0 约 0.0164、rank40 约 0.0100，整个跨度只有 0.0064；
    方名加 0.15 意味着「命中方名即置顶」。

    这不是笔误，是业务语义：中医问诊里方名/病名是强信号，
    精确命中就该排在语义相似度之前。

    **不要试图把它改成量纲整齐的形式**（乘性、归一化都试过，见下表）。

    ⚠ 下面这组实测是 2026-10-27 测的，当时**索引里的 dense 向量是全零的**
    （ONNX 推理静默失败，后已加向量抽查防护）。也就是说这组对比是在
    「dense 实际失效」的基线上测的，**结论需要在好基线上重验**。
    当时的数据：
      加法置顶      hit@1=51.4%  hit@5=91.4%  MRR=0.664
      改成乘性      hit@1=51.4%  hit@5=80.0%  MRR=0.617   ← 明显退化
      先归一化再加法  hit@1=57.1%  hit@5=82.9%  MRR=0.667   ← 同样退化
      关掉症状加分    hit@1=54.3%  hit@5=80.0%  MRR=0.631   ← 退化更明显

    2026-10-28 修好索引（dense 真正生效）后重扫 classics 折扣：
      0.85 → hit@1=55.4% hit@3=75.4% hit@5=**81.5%** MRR=0.657  ← 当前值
      0.92 → hit@1=**56.9%** hit@3=**76.9%** hit@5=78.5% MRR=**0.665**
      1.00 → hit@1=47.7% hit@3=72.3% hit@5=75.4% MRR=0.595
      0.78 → hit@1=55.4% hit@3=72.3% hit@5=**81.5%** MRR=0.653
    0.85 的 hit@5 仍最优；但**上面那组 boost 的对比（乘性/归一化/关症状加分）
    还没在好基线上重验**，动之前先重测。

    归一化会把 RRF 的长尾压平，反而让弱相关 chunk 的 base 仍然很高（≈0.6），
    加分不够「置顶」。

    真正的改进方向是提升实体抽取的准确率，以及让抽象概念类
    （如「太阳伤寒的证候和治法」）能命中正确的篇目 —— 而不是动这里的量纲。
    """
    symptoms = symptoms or []
    if not (formulas or diseases or acupoints or symptoms):
        return

    fset, dset, aset, sset = set(formulas), set(diseases), set(acupoints), set(symptoms)

    for h in hits:
        boost = 0.0
        p = h.payload
        if fset and set(p.get("formula_names") or []) & fset:
            boost += settings.rag_formula_boost
        if dset and set(p.get("diseases") or []) & dset:
            boost += settings.rag_disease_boost
        if aset and set(p.get("acupoints") or []) & aset:
            boost += settings.rag_disease_boost
        if sset and set(p.get("symptoms") or []) & sset:
            # 症状是最弱信号，权重压低，只用于同分时的次序调整
            boost += settings.rag_symptom_boost

        if boost:
            h.boosted = boost
            h.rrf_score += boost
            h.score = h.rrf_score

    hits.sort(key=lambda x: -x.rrf_score)


def dedupe_by_doc(hits: list[Hit], *, max_per_doc: int = 3) -> list[Hit]:
    """同一文档最多保留 N 条，避免一个长文档霸占所有名额。"""
    counter: dict[str, int] = {}
    out: list[Hit] = []
    for h in hits:
        doc = str(h.payload.get("doc_id", ""))
        if counter.get(doc, 0) >= max_per_doc:
            continue
        counter[doc] = counter.get(doc, 0) + 1
        out.append(h)
    return out
