"""中文分词与稀疏向量。

关键约束：分词器版本或自定义词典一变，稀疏索引的 term→id 映射就失效。
构建时把 signature 写进 kb_meta，查询时校验，不一致直接报错，绝不静默降级。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path

import jieba
import mmh3

from app.rag.extract.dicts import CONFIG_DIR, Dicts

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_loaded = False


def init_jieba(config_dir: Path | None = None) -> Dicts:
    """加载自定义词典，保证方名/穴位名不被切碎。"""
    global _loaded
    d = config_dir or CONFIG_DIR
    dicts = Dicts.load(d)
    for name in ("formula_dict.txt", "acupoint_dict.txt", "disease_dict.txt", "symptom_dict.txt"):
        path = d / name
        if path.exists():
            jieba.load_userdict(str(path))
            logger.info("jieba userdict loaded: %s", name)
    for w in dicts.stopwords:
        if len(w) == 1:
            continue
    _loaded = True
    return dicts


def sparse_signature(dicts: Dicts) -> str:
    raw = f"{jieba.__version__}|{sorted(dicts.file_hashes.items())}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _keep(token: str, stopwords: set[str]) -> bool:
    t = token.strip()
    if not t or t in stopwords:
        return False
    if _PUNCT_RE.match(t):
        return False
    if len(t) == 1 and not t.isalnum():
        return False
    return True


def tokenize(text: str, stopwords: set[str] | None = None) -> list[str]:
    stop = stopwords or set()
    return [t for t in jieba.lcut(text) if _keep(t, stop)]


def to_sparse(
    text: str, stopwords: set[str] | None = None
) -> tuple[list[int], list[float]]:
    """返回 (indices, values)。values 为词频，IDF 由 Qdrant 侧 modifier=IDF 计算。"""
    tokens = tokenize(text, stopwords)
    if not tokens:
        return [], []
    counter = Counter(tokens)
    idx: list[int] = []
    val: list[float] = []
    for term, tf in counter.items():
        idx.append(mmh3.hash(term, signed=False))
        val.append(float(tf))
    return idx, val


def keywords(text: str, stopwords: set[str] | None = None, top: int = 12) -> list[str]:
    """高频词作为兜底检索键。"""
    tokens = tokenize(text, stopwords)
    if not tokens:
        return []
    counter = Counter(tokens)
    # 长词优先，再按频次
    ranked = sorted(counter.items(), key=lambda kv: (len(kv[0]) * 2 + kv[1]), reverse=True)
    return [w for w, _ in ranked[:top]]
