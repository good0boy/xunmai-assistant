"""Embedding 冒烟测试：验证模型可用、维度正确、语义相似度合理。

用法：python -m scripts.test_embed
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402


def main() -> None:
    from fastembed import TextEmbedding

    print("fastembed imported")
    t0 = time.time()
    model = TextEmbedding(
        model_name="BAAI/bge-small-zh-v1.5", cache_dir="./_local/models", threads=4
    )
    print(f"model loaded in {time.time() - t0:.1f}s")
    print("has query_embed:", hasattr(model, "query_embed"))

    docs = [
        "太阳之为病，脉浮，头项强痛而恶寒。",
        "桂枝汤主治太阳中风，头痛发热汗出恶风。",
        "第四章讨论的是本草的炮制方法与四气五味理论。",
    ]
    t1 = time.time()
    vecs = [np.array(v) for v in model.embed(docs)]
    print(f"embed {len(docs)} docs in {time.time() - t1:.2f}s, dim={len(vecs[0])}")

    query = "感冒了怕冷没有汗，脖子后面疼"
    t2 = time.time()
    if hasattr(model, "query_embed"):
        q = np.array(next(iter(model.query_embed(query))))
        print(f"query_embed in {time.time() - t2:.2f}s")
    else:
        q = np.array(next(iter(model.embed([query]))))
        print("fallback to embed for query")

    def cos(a: np.ndarray, b: np.ndarray) -> float:
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))

    print(f"\nquery: {query}")
    for i, d in enumerate(docs):
        print(f"  sim[{i}] = {cos(vecs[i], q):.4f}  | {d[:28]}")
    print(f"\n文档间相似度[0,1] = {cos(vecs[0], vecs[1]):.4f}")
    print(f"文档间相似度[0,2] = {cos(vecs[0], vecs[2]):.4f}")


if __name__ == "__main__":
    main()
