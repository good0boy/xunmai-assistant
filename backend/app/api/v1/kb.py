"""知识库检索调试端点。这是调参和排障的主要入口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.config import settings
from app.rag.index import qdrant_store
from app.rag.prompts.assembler import source_label as make_label
from app.rag.retrieve import pipeline

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="检索问题"),
    debug: int = Query(0, ge=0, le=1),
    max_priority: int | None = Query(None, ge=1, le=5),
) -> dict[str, Any]:
    """检索调试。debug=1 返回每一级的召回数、分数、降级原因。"""
    result = await pipeline.retrieve(q, max_priority=max_priority)

    hits = [
        {
            "n": i + 1,
            "chunk_id": h.chunk_id,
            "score": round(h.rrf_score, 5),
            "dense": round(h.dense_score, 4) if h.dense_score is not None else None,
            "sparse": round(h.sparse_score, 4) if h.sparse_score is not None else None,
            "boost": round(h.boosted, 3),
            "rerank": h.rerank_score,
            "doc_type": h.payload.get("doc_type"),
            "source": h.payload.get("source_file"),
            "section": h.section_path_str,
            "from_prompts": make_label(h) if debug == 0 else None,
            "formulas": h.payload.get("formula_names"),
            "symptoms": h.payload.get("symptoms"),
            "diseases": h.payload.get("diseases"),
            "case_serial": h.payload.get("case_serial"),
            "chars": len(h.text),
            "text": h.text[:600] if debug else h.text[:200],
        }
        for i, h in enumerate(result.hits)
    ]

    payload: dict[str, Any] = {
        "query": q,
        "level": result.level,
        "covered": result.covered,
        "hit_count": len(hits),
        "hits": hits,
    }
    if debug:
        payload["trace"] = result.to_trace_payload()
    return payload


@router.get("/docs")
async def docs() -> dict[str, Any]:
    """按文档汇总已入库的知识块分布。"""
    from qdrant_client.http import models as qm

    client = qdrant_store.get_client()
    try:
        points, _ = client.scroll(
            collection_name=settings.qdrant_collection,
            limit=8000,
            with_payload=["source_file", "doc_type", "source_priority", "chapter_no"],
            with_vectors=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "items": []}

    agg: dict[str, dict[str, Any]] = {}
    for p in points:
        pl = p.payload or {}
        src = str(pl.get("source_file", "?"))
        item = agg.setdefault(
            src,
            {
                "source_file": src,
                "doc_type": pl.get("doc_type"),
                "priority": pl.get("source_priority"),
                "chunks": 0,
                "chapters": set(),
            },
        )
        item["chunks"] += 1
        if pl.get("chapter_no"):
            item["chapters"].add(int(pl["chapter_no"]))

    items = []
    for v in agg.values():
        v["chapters"] = sorted(v["chapters"])
        items.append(v)
    items.sort(key=lambda x: (x["priority"] or 9, x["source_file"]))

    return {"total": len(points), "items": items}


@router.get("/chunks/{chunk_id}")
async def chunk_detail(chunk_id: str, with_neighbors: int = Query(1, ge=0, le=3)) -> dict[str, Any]:
    """单块详情，含前后邻块。

    chunk_id 在三个集合里都可能存在（判语是字幕聚合块，id 形如 x:0），
    所以要**按顺序试遍所有分组**，不能只查 tcm_kb ——
    否则经典原文与判语的引用查不到原文，前端「相邻内容」永远是空的。
    """
    from qdrant_client.http import models as qm

    from app.rag.index import sources

    client = qdrant_store.get_client()
    payload: dict[str, Any] | None = None
    for group, collection in pipeline._kb_groups():
        res = client.scroll(
            collection_name=collection,
            scroll_filter=qm.Filter(
                must=[qm.FieldCondition(key="chunk_id", match=qm.MatchValue(value=chunk_id))]
            ),
            limit=1,
            with_payload=True,
        )
        points, _ = res
        if points:
            payload = points[0].payload or {}
            break
    if payload is None:
        return {"error": "not found"}

    data: dict[str, Any] = {"payload": payload}

    if with_neighbors > 0:
        from app.rag.retrieve.hybrid import Hit

        h = Hit(chunk_id=chunk_id, score=0.0, payload=payload)
        data["neighbors"] = await pipeline.neighbor_expand(h, span=with_neighbors)

    return data
