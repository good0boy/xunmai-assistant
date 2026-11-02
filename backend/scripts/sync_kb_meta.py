"""从 Qdrant 实际状态反推 kb_meta，写回 SQLite。

适用场景：**只上传了向量索引、没有带 data/app.db** 的部署 ——
向量集合都在（points 正常），但 SQLite 的 kb_meta 是空的，
/health 报 kb_status=stale（build_version=null）。

这时不需要重建索引（9 分钟），跑本脚本几秒补齐元信息：

    docker compose run --rm backend python -m scripts.sync_kb_meta

元信息的每一项都从当前真实状态计算（Qdrant 点数、语料指纹、模型维度、
分词签名），与 build_index 写入的内容同源。
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")


async def main() -> None:
    from app.core.config import settings
    from app.core.logging import setup_logging
    from app.db.session import dispose_db, init_db
    from app.rag.extract.tokenize import init_jieba, sparse_signature
    from app.rag.index import qdrant_store
    from app.rag.index.meta import corpus_hash, expected_build_version, write_meta

    setup_logging(settings.log_level)
    await init_db()

    dicts = init_jieba()
    groups = {
        "nihai": settings.qdrant_collection,
        "classics": settings.qdrant_collection_classics,
        "judges": settings.qdrant_collection_judges,
    }
    counts: dict[str, int] = {}
    for group, coll in groups.items():
        try:
            counts[group] = int(qdrant_store.get_client().count(coll, exact=True).count)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {coll}: {exc}")
            print("  Qdrant 里没有这个集合 —— 索引不完整，请先重建：make kb")
            await dispose_db()
            sys.exit(1)

    pairs = {
        "build_version": expected_build_version(),
        "corpus_hash": corpus_hash(),
        "doc_count": str(counts["nihai"]),
        "external_doc_count": str(counts["classics"] + counts["judges"]),
        "external_groups": (
            f'{{"classics": {counts["classics"]}, "judges": {counts["judges"]}}}'
        ),
        "embed_model": settings.embed_model,
        "embed_dim": str(settings.embed_dim),
        "sparse_signature": sparse_signature(dicts),
        "query_prefix": settings.embed_query_prefix,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    await write_meta(pairs)
    await dispose_db()

    total = sum(counts.values())
    print(f"kb_meta 已补写：三集合 {counts} 共 {total} 点")
    print("刷新页面或 curl /api/v1/health，kb_status 应为 ready")


if __name__ == "__main__":
    asyncio.run(main())
