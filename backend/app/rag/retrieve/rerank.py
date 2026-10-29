"""LLM 二阶段重排。

不用 cross-encoder 的原因：中文 reranker 可选面窄、CPU 延迟高，
而且 LLM 能判断"这段是不是倪师真讲过的内容"，纯语义相似度模型做不到。
"""

from __future__ import annotations

import hashlib
import logging
import time

from app.core.config import settings
from app.llm.client import get_client
from app.rag.retrieve.hybrid import Hit

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_CACHE_TTL = 7 * 24 * 3600
_CACHE_MAX = 2048

SYSTEM = (
    "你是中医古籍检索相关性裁判。给定用户问题和候选知识片段，"
    "判断每个片段对回答该问题的价值。\n"
    "打分标准：\n"
    "3 = 直接回答（含明确的方剂、证型、条文或处置）\n"
    "2 = 部分相关（提供病机、症状或相关背景）\n"
    "1 = 仅主题相近\n"
    "0 = 无关\n"
    '只输出 JSON：{"scores":[{"id":"片段编号","score":0-3,"reason":"不超过15字"}]}'
)


def _truncate(text: str, head: int = 300, tail: int = 200) -> str:
    text = text.strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}…{text[-tail:]}"


def _cache_key(query: str, ids: list[str]) -> str:
    raw = query + "|" + ",".join(ids)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict[str, int] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: dict[str, int]) -> None:
    if len(_CACHE) > _CACHE_MAX:
        # 简单淘汰：清掉最早的一批
        for k in list(_CACHE)[: _CACHE_MAX // 4]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time(), val)


async def rerank(
    query: str,
    hits: list[Hit],
    *,
    top_n: int | None = None,
    min_score: int | None = None,
) -> list[Hit]:
    """对候选打 0-3 分，过滤低分，按分数重排。失败时原样返回（不阻断主链路）。"""
    if not hits:
        return hits
    if settings.rag_rerank_backend == "none":
        return hits

    n = top_n or settings.rag_rerank_top_n
    threshold = settings.rag_rerank_min_score if min_score is None else min_score
    candidates = hits[:n]
    rest = hits[n:]

    key = _cache_key(query, [h.chunk_id for h in candidates])
    cached = _cache_get(key)

    if cached is not None:
        scores = cached
    else:
        listing = "\n\n".join(
            f"[{i + 1}] {_truncate(h.text)}" for i, h in enumerate(candidates)
        )
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"用户问题：{query}\n\n候选片段：\n{listing}"},
        ]
        try:
            data = await get_client().json_chat(messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank 调用失败，跳过重排: %s", exc)
            return hits

        scores: dict[str, int] = {}
        for item in data.get("scores") or []:
            try:
                idx = int(str(item.get("id", "")).strip())
                sc = int(item.get("score", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= len(candidates):
                scores[candidates[idx - 1].chunk_id] = max(0, min(3, sc))
        if not scores:
            logger.warning("rerank 未返回有效分数，跳过")
            return hits
        _cache_put(key, scores)

    kept: list[Hit] = []
    for h in candidates:
        sc = scores.get(h.chunk_id)
        if sc is None:
            # 模型漏评的候选按低分保留，避免误杀
            h.rerank_score = 1.0
            kept.append(h)
            continue
        h.rerank_score = float(sc)
        if sc >= threshold:
            kept.append(h)

    kept.sort(key=lambda x: (-(x.rerank_score or 0), -x.rrf_score))
    return kept + rest
