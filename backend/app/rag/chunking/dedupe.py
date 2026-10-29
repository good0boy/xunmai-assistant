"""SimHash 近似去重。

SKILL.md 里大量内容是 modules 的摘录，直接入库会让同一段话占掉两个 top-k 名额。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import mmh3

from app.rag.extract.tokenize import tokenize

if TYPE_CHECKING:
    from app.rag.chunking.schema import Chunk

_BITS = 64


def simhash(text: str, stopwords: set[str] | None = None) -> int:
    tokens = tokenize(text, stopwords)
    if not tokens:
        return 0
    vec = [0] * _BITS
    for t in tokens:
        h = mmh3.hash64(t, signed=False)[0]
        for i in range(_BITS):
            vec[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(_BITS):
        if vec[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def dedupe(
    chunks: list[Chunk], *, stopwords: set[str] | None = None, max_distance: int = 3
) -> tuple[list[Chunk], int]:
    """返回 (保留的 chunk, 被丢弃的数量)。

    用 4×16bit 分桶：汉明距离 ≤3 时，至少有一个 16 位段完全相同（鸽笼原理），
    所以只需在同桶内比较，避免 O(n²) 全量扫描。
    """
    # 按 (doc_type, source_priority) 排序，优先保留权威来源
    ordered = sorted(chunks, key=lambda c: (c.source_priority, c.doc_type, c.ordinal))

    buckets: dict[tuple[int, int], list[tuple[int, Chunk]]] = {}
    kept: list[Chunk] = []
    dropped = 0

    for ch in ordered:
        h = simhash(ch.text, stopwords)
        dup = False
        for band in range(4):
            key = (band, (h >> (band * 16)) & 0xFFFF)
            for other_h, _other in buckets.get(key, []):
                if hamming(h, other_h) <= max_distance:
                    dup = True
                    break
            if dup:
                break

        if dup:
            dropped += 1
            continue

        kept.append(ch)
        for band in range(4):
            key = (band, (h >> (band * 16)) & 0xFFFF)
            buckets.setdefault(key, []).append((h, ch))

    return kept, dropped
