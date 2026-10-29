"""检索编排：召回 → 融合 → 加分 → 重排 → 降级。

四级降级链对应 SKILL.md 的要求：
  L1 精确混合检索 → L2 同义词扩展 → L3 放宽约束的宽召回 → L4 声明未覆盖
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.errors import KBUnavailableError
from app.llm.client import get_client
from app.rag.extract.dicts import Dicts
from app.rag.extract.entities import EntityExtractor, Extracted
from app.rag.extract.tokenize import to_sparse
from app.rag.index import qdrant_store, sources
from app.rag.index.embedder import embed_query
from app.rag.retrieve.hybrid import (
    Hit,
    apply_entity_boost,
    dedupe_by_doc,
    rrf_fuse,
    search_dense,
    search_sparse,
)
from app.rag.retrieve.rerank import rerank

logger = logging.getLogger(__name__)


@dataclass
class Trace:
    level: int
    label: str
    query: str
    dense: int = 0
    sparse: int = 0
    fused: int = 0
    after_rerank: int = 0
    top_score: float = 0.0
    note: str = ""
    # 分段耗时，用来区分「检索慢」还是「模型慢」
    embed_ms: float = 0.0
    search_ms: float = 0.0
    rerank_ms: float = 0.0


@dataclass
class RetrievalResult:
    hits: list[Hit] = field(default_factory=list)
    level: int = 4
    traces: list[Trace] = field(default_factory=list)
    entities: Extracted | None = None

    @property
    def covered(self) -> bool:
        return bool(self.hits)

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "degradation_level": self.level,
            "covered": self.covered,
            "entities": {
                "formulas": self.entities.formulas if self.entities else [],
                "diseases": self.entities.diseases if self.entities else [],
                "symptoms": self.entities.symptoms if self.entities else [],
                "acupoints": self.entities.acupoints if self.entities else [],
            },
            "traces": [
                {
                    "level": t.level,
                    "label": t.label,
                    "query": t.query,
                    "dense": t.dense,
                    "sparse": t.sparse,
                    "fused": t.fused,
                    "after_rerank": t.after_rerank,
                    "top_score": round(t.top_score, 4),
                    "note": t.note,
                    "embed_ms": round(t.embed_ms, 1),
                    "search_ms": round(t.search_ms, 1),
                    "rerank_ms": round(t.rerank_ms, 1),
                }
                for t in self.traces
            ],
        }


_extractor: EntityExtractor | None = None
_dicts: Dicts | None = None


def set_extractor(extractor: EntityExtractor, dicts: Dicts) -> None:
    global _extractor, _dicts
    _extractor = extractor
    _dicts = dicts


def get_extractor() -> EntityExtractor:
    global _extractor, _dicts
    if _extractor is None:
        from app.rag.extract.tokenize import init_jieba

        _dicts = init_jieba()
        _extractor = EntityExtractor(_dicts)
    return _extractor


def _query_variants_rules(query: str, entities: Extracted, dicts: Dicts) -> list[str]:
    """规则式查询扩展。把口语化描述映射到中医术语。

    口语词在语料里往往不是原样出现的（"睡不着" → 语料讲"失眠"），
    所以命中某组 aliases 时，要同时补上 canonical 和组内其它常见说法。
    """
    variants = [query]
    expanded: list[str] = []

    for group in _get_synonyms():
        for canonical, aliases in group.items():
            if not any(a in query for a in aliases):
                continue
            if canonical not in query:
                expanded.append(canonical)
            # 组内其它说法也补上，口语与书面语常常都命中不同段落
            for a in aliases:
                if a not in query and len(a) >= 2 and a not in expanded:
                    expanded.append(a)

    if expanded:
        variants.append(query + " " + " ".join(expanded[:8]))

    # 有实体时补一条纯实体查询，提升精确匹配权重
    ents = " ".join(
        (entities.formulas[:3] + entities.diseases[:2] + entities.symptoms[:4])
    ).strip()
    if ents and ents not in query:
        variants.append(ents)

    return variants[:3]


_SYNONYMS: list[dict[str, list[str]]] | None = None


def _get_synonyms() -> list[dict[str, list[str]]]:
    global _SYNONYMS
    if _SYNONYMS is None:
        try:
            import yaml

            from app.rag.extract.dicts import CONFIG_DIR

            path = CONFIG_DIR / "synonyms.yaml"
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                groups = data.get("groups") or []
                _SYNONYMS = [
                    {g["canonical"]: g.get("aliases", [])}
                    for g in groups
                    if isinstance(g, dict) and g.get("canonical")
                ]
            else:
                _SYNONYMS = []
        except Exception as exc:  # noqa: BLE001
            logger.warning("同义词表加载失败: %s", exc)
            _SYNONYMS = []
    return _SYNONYMS


def _kb_groups() -> list[tuple[str, str]]:
    """知识库分组 → (分组名, 集合名)。顺序即合并优先级。

    **判语组不参与召回**：判语与倪师解读同源，从来不当独立证据；
    它唯一的用途是给倪师命中挂「B 站出处」，而挂载靠条文号精确匹配
    （`_judges_by_article` 按 payload 索引直查，几毫秒）。
    让 927 块判语走完整 dense+sparse 召回，等于花 1/3 检索耗时
    买一个几乎不用、还偶尔挂错的结果（dense-gap 兜底曾把
    「条35 葛根汤」挂到视频第 17 秒）—— 已删除，见 `_attach_judge_cites`。
    """
    return [
        (sources.NIHAI_GROUP, settings.qdrant_collection),
        (sources.CLASSICS_GROUP, settings.qdrant_collection_classics),
    ]


def _judges_by_article(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], Hit]:
    """按 (典籍, 条文号) 直接查判语集合。

    不走检索召回 —— 判语段能不能被召回是检索的事，
    「倪师讲的第 35 条」和「判语里的第 35 条」对得上就该给出出处。

    **必须带典籍（origin）联合匹配**：伤寒论与金匮要略的条文号各自独立编号，
    只按条文号匹配会把金匮的「条35」挂到伤寒论判语上 —— 跨典籍错挂。
    origin 为空的倪师块直接放弃（宁可挂不上）。
    """
    from qdrant_client.http import models as qm

    if not pairs:
        return {}
    articles = sorted({a for _, a in pairs})
    try:
        client = qdrant_store.get_client()
        pts, _ = client.scroll(
            collection_name=settings.qdrant_collection_judges,
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="article_no", match=qm.MatchAny(any=articles)
                    )
                ]
            ),
            limit=200,
            with_payload=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("按条文号查判语失败: %s", exc)
        return {}
    out: dict[tuple[str, str], Hit] = {}
    for p in pts:
        payload = p.payload or {}
        key = (str(payload.get("origin") or ""), str(payload.get("article_no") or ""))
        if key[0] and key[1] and key in pairs and key not in out:
            out[key] = Hit(
                chunk_id=str(payload.get("chunk_id", "")),
                score=0.0,
                payload=payload,
            )
    return out


def _attach_judge_cites(nihai_hits: list[Hit]) -> None:
    """给倪师解读的命中挂上判语出处（B 站视频号 + 时间戳）。

    判语库与现有库同源（都是倪师讲义），所以判语**不作为独立证据参与排名**，
    只在引用展示时补一条「可回放核对的原话出处」。

    匹配**只用条文号精确对应**（倪师块「条文35」↔ 判语段「条35」，
    按判语集合的 payload 索引直查，不依赖召回）。

    曾经试过两条更"聪明"的匹配，全部放弃，留档防止将来再走回头路：

    - **字符 2-gram 重叠率**：倪师解读是现代口语讲解、判语是字幕级口语，
      字面重合天然很低 —— 阈值 0.20 时 60 个样本只挂上 1 条且是错的，
      与随机交叉基线（p50=0.035 / p90=0.079）完全重叠，区分不开。
    - **dense 相似度接近**（同一查询召回的分数差 < 0.02 即算同一件事）：
      能挂上，但语义太弱 —— "分数接近"只说明讲同一类话题，
      不说明讲的是同一条，实际挂出过「条35 葛根汤 → 视频第 17 秒」
      这种错误出处。**误挂出处比没有出处更糟**，宁可挂不上。
    """
    if not nihai_hits:
        return
    pairs = {
        (str(h.payload.get("origin") or ""), str(h.payload.get("article_no") or ""))
        for h in nihai_hits
        if h.payload.get("article_no") and h.payload.get("origin")
    }
    if not pairs:
        return
    by_article = _judges_by_article(pairs)
    if not by_article:
        return
    for h in nihai_hits:
        key = (
            str(h.payload.get("origin") or ""),
            str(h.payload.get("article_no") or ""),
        )
        if key[0] and key[1] and key in by_article:
            cites = by_article[key].payload.get("judge_cites") or []
            if cites:
                h.payload["judge_cites"] = cites
                h.payload["judge_match"] = "article"


def _merge_groups(
    nihai_hits: list[Hit],
    classic_hits: list[Hit],
    *,
    limit: int,
    nihai_ratio: float = 0.6,
) -> list[Hit]:
    """合并倪师解读与经典原文。

    名额分配：倪师解读保底占 ``limit * nihai_ratio``（本项目的核心资产 ——
    表达风格、辨证抓手都在这里），其余名额给经典原文。
    倪师组没占满时，经典原文可以吃满剩余（倪师组命中少就该让位）。

    同源规则：
    - 经典原文里与倪师组同一典籍（origin 相同）的部分跳过，避免同一知识点
      的两种呈现重复占引用名额；
    - 经典组内部同源只留一条，防止一本书刷屏；
    - 倪师组内部**不做同源去重** —— 同一典籍的多个段落都要保留。
    """
    nihai_origins = {
        str(h.payload.get("origin") or "")
        for h in nihai_hits
        if str(h.payload.get("origin") or "")
    }

    nihai_cap = int(limit * nihai_ratio)
    picked: list[Hit] = list(nihai_hits[:nihai_cap])
    used_classic_origins: set[str] = set()

    scale = settings.rag_classics_score_scale
    for h in classic_hits:
        if len(picked) >= limit:
            break
        o = str(h.payload.get("origin") or "")
        if o and o in nihai_origins:
            continue  # 同源 → 让位给倪师解读
        if o and o in used_classic_origins:
            continue  # 经典组内部同源只留一条
        h.rrf_score *= scale
        h.score = h.rrf_score
        picked.append(h)
        if o:
            used_classic_origins.add(o)

    # 倪师组没占满保底名额时，用它的其余命中补上
    for h in nihai_hits[nihai_cap:]:
        if len(picked) >= limit:
            break
        picked.append(h)

    picked.sort(key=lambda h: -h.rrf_score)
    return picked


def _run_hybrid(
    variants: list[str],
    *,
    max_priority: int,
    top_k_dense: int,
    top_k_sparse: int,
) -> tuple[dict[str, list[Hit]], dict[str, float]]:
    """按分组各自召回 + 组内 RRF 融合。

    「模型加载失败」和「向量库瞬时故障」必须分开处理：
    前者是离线集群拉不到 ONNX 模型，要永久降级为纯关键词；
    后者只是一次抖动，绝不能把 dense 通道永久禁用 ——
    否则 Qdrant 抖一下，整个进程余生都退化成关键词检索。
    """
    from app.rag.index import embedder

    # 向量库不可达 vs 集合不存在，必须区分：
    # 不可达时静默跳过所有分组 → 检索返回 0 条 → 用户被告知「本知识库没覆盖」，
    # 真实原因是向量库挂了。这是「悄悄产生错误结果」的典型。
    if not qdrant_store.ping():
        raise KBUnavailableError("向量库不可达，无法检索")

    group_hits: dict[str, list[Hit]] = {}
    embed_ms = 0.0
    search_ms = 0.0

    for group, collection in _kb_groups():
        if not qdrant_store.collection_exists(collection):
            logger.warning("集合 %s 不存在，跳过该分组（其余分组仍会检索）", collection)
            continue
        lists: list[list[Hit]] = []
        weights: list[float] = []

        for v in variants:
            if embedder.dense_available():
                t0 = time.perf_counter()
                try:
                    vec = embed_query(v)
                except Exception as exc:  # noqa: BLE001
                    # 模型下不来（离线集群）时不能整个检索挂掉，
                    # 降级为纯关键词检索，语义召回变差但服务仍可用
                    embedder.mark_dense_unavailable(str(exc))
                else:
                    embed_ms += (time.perf_counter() - t0) * 1000
                    t1 = time.perf_counter()
                    try:
                        lists.append(
                            search_dense(
                                vec,
                                limit=top_k_dense,
                                max_priority=max_priority,
                                collection=collection,
                            )
                        )
                        weights.append(settings.rag_vector_weight)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "dense 检索失败（%s），本次跳过（不改全局 dense 状态）: %s",
                            collection,
                            exc,
                        )
                    search_ms += (time.perf_counter() - t1) * 1000

            t2 = time.perf_counter()
            idx, val = to_sparse(v)
            lists.append(
                search_sparse(
                    idx,
                    val,
                    limit=top_k_sparse,
                    max_priority=max_priority,
                    collection=collection,
                )
            )
            weights.append(settings.rag_sparse_weight)
            search_ms += (time.perf_counter() - t2) * 1000

        if lists:
            group_hits[group] = rrf_fuse(lists, k=settings.rag_rrf_k, weights=weights)

    timings = {"embed_ms": embed_ms, "search_ms": search_ms}
    return group_hits, timings


async def _level_retrieve(
    level: int,
    label: str,
    variants: list[str],
    entities: Extracted,
    *,
    max_priority: int,
    top_k_dense: int,
    top_k_sparse: int,
    need_rerank: bool,
) -> tuple[list[Hit], Trace]:
    group_hits, timings = await asyncio.to_thread(
        _run_hybrid,
        variants,
        max_priority=max_priority,
        top_k_dense=top_k_dense,
        top_k_sparse=top_k_sparse,
    )

    nihai_hits = group_hits.get(sources.NIHAI_GROUP) or []
    classic_hits = group_hits.get(sources.CLASSICS_GROUP) or []

    # 判语不作为独立证据（同源），只用来给倪师命中补出处
    _attach_judge_cites(nihai_hits)

    # 实体加分必须先于跨组合并：加分是绝对分（「命中方名即置顶」），
    # 合并前加才能让经典原文里命中方名的片段也有机会进候选。
    for group_hits_list in (nihai_hits, classic_hits):
        apply_entity_boost(
            group_hits_list,
            formulas=entities.formulas,
            diseases=entities.diseases,
            acupoints=entities.acupoints,
            symptoms=entities.symptoms,
        )

    # 跨组合并：按分数竞争，同源 classics 让位给倪师解读
    merged = _merge_groups(
        nihai_hits,
        classic_hits,
        limit=max(settings.rag_rerank_top_n * 2, settings.rag_final_k * 3),
        nihai_ratio=settings.rag_nihai_ratio,
    )

    trace = Trace(
        level=level,
        label=label,
        query=variants[0],
        fused=len(merged),
        embed_ms=timings["embed_ms"],
        search_ms=timings["search_ms"],
    )
    if not merged:
        trace.note = "无召回"
        return [], trace

    merged = dedupe_by_doc(merged, max_per_doc=settings.rag_max_chunks_per_doc)
    trace.top_score = merged[0].rrf_score if merged else 0.0

    if need_rerank:
        t0 = time.perf_counter()
        merged = await rerank(variants[0], merged)
        trace.rerank_ms = (time.perf_counter() - t0) * 1000
        trace.after_rerank = len(merged)
        if merged:
            trace.top_score = max(trace.top_score, merged[0].rrf_score)

    return merged, trace


_signature_verified = False


async def _verify_sparse_signature(dicts: Any) -> bool:
    """校验索引的稀疏签名与当前分词器/词典是否一致。

    `tokenize.py` 的模块注释承诺「构建时写入 kb_meta，查询时校验，不一致直接
    报错，绝不静默降级」—— 但这个校验从来没实现过。改了 jieba 版本或自定义
    词典后不重建索引，稀疏通道的召回会无声下降。

    这里只告警不抛错（抛错等于服务不可用，太激进），但会把结果标进 trace，
    让「检索质量下降」这件事可观测。
    """
    global _signature_verified
    if _signature_verified:
        return True
    _signature_verified = True
    try:
        from app.rag.extract.tokenize import sparse_signature
        from app.rag.index.meta import read_meta

        actual = await read_meta("sparse_signature")
        if not actual:
            return True
        expected = sparse_signature(dicts)
        if actual != expected:
            logger.error(
                "稀疏签名不一致（索引 %s vs 当前 %s）：分词器或词典变了，"
                "必须重建索引，否则稀疏召回无声下降",
                actual,
                expected,
            )
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("稀疏签名校验失败: %s", exc)
    return True


async def retrieve(
    query: str,
    *,
    max_priority: int | None = None,
) -> RetrievalResult:
    """主检索入口。max_priority 越小越严格（1=最权威, 5=含研究资料）。"""
    extractor = get_extractor()
    dicts = _dicts

    await _verify_sparse_signature(dicts)

    # 先把口语映射成中医术语再抽实体，否则"怕冷/没汗/脖子僵"一个实体都抽不到
    normalized = _normalize_query(query)
    entities = extractor.extract(normalized)
    result = RetrievalResult(entities=entities)

    prio = max_priority if max_priority is not None else 4
    variants = (
        _query_variants_rules(query, entities, dicts)
        if settings.rag_query_rewrite != "off" and dicts
        else [query]
    )

    # ---------- L1 精确混合 ----------
    hits, trace = await _level_retrieve(
        1,
        "精确混合检索",
        variants,
        entities,
        max_priority=prio,
        top_k_dense=settings.rag_top_k_dense,
        top_k_sparse=settings.rag_top_k_sparse,
        need_rerank=settings.rag_rerank_backend != "none",
    )
    result.traces.append(trace)
    if _satisfied(hits, level=1, entities=entities):
        result.hits = _finalize(hits)
        result.level = 1
        return result

    if settings.rag_degradation_max_level < 2:
        result.level = 4
        return result

    # ---------- L2 同义词 / 术语扩展 ----------
    syn_variants = _expand_with_synonyms(query, entities)
    if syn_variants != variants:
        hits2, trace2 = await _level_retrieve(
            2,
            "同义词扩展",
            syn_variants,
            entities,
            max_priority=prio,
            top_k_dense=settings.rag_top_k_dense,
            top_k_sparse=settings.rag_top_k_sparse,
            need_rerank=False,
        )
        result.traces.append(trace2)
        if hits2:
            result.hits = _finalize(hits2)
            result.level = 2
            return result
    else:
        result.traces.append(
            Trace(level=2, label="同义词扩展", query=query, note="无可用扩展词，跳过")
        )

    if settings.rag_degradation_max_level < 3:
        result.level = 4
        return result

    # ---------- L3 宽召回（放宽权威度 + 只保留核心实体） ----------
    wide_query = " ".join(
        entities.formulas[:3] + entities.diseases[:2] + entities.symptoms[:4]
    ).strip() or query
    wide_entities = Extracted(
        formulas=entities.formulas[:2],
        diseases=entities.diseases[:1],
        symptoms=entities.symptoms[:3],
    )
    hits3, trace3 = await _level_retrieve(
        3,
        "宽召回",
        [wide_query],
        wide_entities,
        max_priority=5,
        top_k_dense=settings.rag_top_k_dense * 2,
        top_k_sparse=settings.rag_top_k_sparse * 2,
        need_rerank=False,
    )
    result.traces.append(trace3)
    if hits3:
        # 宽召回产物做二次重排，避免噪音进上下文
        hits3 = await rerank(query, hits3, top_n=settings.rag_rerank_top_n)
        if hits3:
            result.hits = _finalize(hits3)
            result.level = 3
            return result

    # ---------- L4 未覆盖 ----------
    result.traces.append(
        Trace(level=4, label="声明未覆盖", query=query, note="四级检索均无有效结果")
    )
    result.level = 4
    return result


def _normalize_query(query: str) -> str:
    """把用户的口语说法替换成典籍里的标准术语。

    "怕冷" 在语料里几乎不出现，"恶寒" 才是。不做这层映射，
    实体抽取在口语提问上基本全空，实体加分也就失效了。
    """
    out = query
    for group in _get_synonyms():
        for canonical, aliases in group.items():
            for a in aliases:
                if a in out and canonical not in out:
                    out = out.replace(a, canonical)
                    break
    return out


def _expand_with_synonyms(query: str, entities: Extracted) -> list[str]:
    variants = [query]
    extra: list[str] = []
    for group in _get_synonyms():
        for canonical, aliases in group.items():
            if any(a in query for a in aliases) and canonical not in query:
                extra.append(canonical)
    if extra:
        variants.append(" ".join(extra + [query]))
    if entities.formulas:
        # 方名 → 类方：直接搜方名本身已能命中，这里补一条去掉修饰的
        base = [f.replace("加", " ") for f in entities.formulas[:2]]
        for b in base:
            if b != entities.formulas[0]:
                variants.append(f"{query} {b}")
    return variants[:3]


def _satisfied(hits: list[Hit], *, level: int, entities: Extracted) -> bool:
    if level == 1:
        if len(hits) >= 2 and hits[0].rrf_score >= 0.025:
            return True
        # 有明确实体命中时，一条强结果也算过
        if hits and entities.formulas and hits[0].boosted > 0:
            return True
        return False
    return bool(hits)


def _finalize(hits: list[Hit]) -> list[Hit]:
    """按字符预算截断，取 top_k。保证至少 1 条。"""
    out: list[Hit] = []
    total = 0
    for h in hits[: settings.rag_final_k]:
        n = len(h.text)
        if out and total + n > settings.rag_max_context_chars:
            break
        out.append(h)
        total += n
    return out


async def neighbor_expand(hit: Hit, *, span: int = 1) -> list[dict[str, Any]]:
    """取同文档相邻块，用于前端展开原文。

    **必须按来源分组选集合** —— 之前一律查 tcm_kb，导致经典原文与判语的
    引用永远查不到原文出处（前端「相邻内容」永远是空的）。
    """
    doc_id = hit.payload.get("doc_id")
    ordinal = hit.payload.get("ordinal")
    if doc_id is None or ordinal is None:
        return []
    from qdrant_client.http import models as qm

    client = qdrant_store.get_client()
    collection = sources.collection_for_group(hit.payload.get("source_group"))
    try:
        res = client.scroll(
            collection_name=collection,
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
                    qm.FieldCondition(
                        key="ordinal",
                        range=qm.Range(gte=int(ordinal) - span, lte=int(ordinal) + span),
                    ),
                ]
            ),
            limit=span * 2 + 1,
            with_payload=True,
        )
        points, _ = res
        return [
            {"ordinal": p.payload.get("ordinal"), "text": p.payload.get("text")}
            for p in sorted(points, key=lambda x: x.payload.get("ordinal", 0))
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("邻块扩展失败: %s", exc)
        return []
