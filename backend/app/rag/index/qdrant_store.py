"""Qdrant 客户端与集合管理。连接失败不抛异常，由调用方按 kb_status 处理。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_mode == "local":
            # 进程内嵌模式：单容器部署用，索引目录随发布包一起交付
            from app.core.paths import prepare_local_index

            path = prepare_local_index(settings.qdrant_path)
            _client = QdrantClient(path=str(path))
            logger.info("qdrant local mode: %s", path)
        else:
            _client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=30,
                prefer_grpc=False,
            )
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
    _client = None


def ping() -> bool:
    """Qdrant 是否可达。

    **必须和「集合不存在」区分开**：之前 collection_exists 在不可达时也返回
    False，检索层就把三个分组全跳过了 → 返回 0 条 → 用户被告知
    「本知识库没有覆盖该问题」，而真实原因是向量库挂了。
    """
    try:
        return bool(get_client().get_collections().collections is not None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qdrant unreachable: %s", exc)
        return False


def collection_exists(collection: str | None = None) -> bool:
    name = collection or settings.qdrant_collection
    try:
        return get_client().collection_exists(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("qdrant unreachable: %s", exc)
        return False


def clear_points(collection: str | None = None) -> None:
    """清空所有点但保留集合结构。

    不用 drop collection：Windows 下 bind mount 到 NTFS 时，删除集合目录会触发
    Permission denied (os error 13)，这是 Qdrant 在 Windows 卷上的已知问题。
    """
    client = get_client()
    name = collection or settings.qdrant_collection
    try:
        client.delete(
            collection_name=name,
            points_selector=qm.FilterSelector(filter=qm.Filter(must=[])),
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear_points failed: %s", exc)
        raise


def ensure_collection(
    recreate: bool = False, collection: str | None = None, size: int | None = None
) -> None:
    """建集合。dense 维度实测探测，sparse 走 IDF 修饰（即 BM25 语义）。

    recreate=True 时只清空点，不删集合。若模型维度变了导致不匹配，需要手动删集合。
    """
    from app.rag.index.embedder import dim as embed_dim

    client = get_client()
    name = collection or settings.qdrant_collection
    size = size or embed_dim()

    if client.collection_exists(name):
        info = client.get_collection(name)
        current = info.config.params.vectors
        cur_size = None
        if isinstance(current, dict):
            dense = current.get(DENSE_VECTOR)
            cur_size = getattr(dense, "size", None)
        if cur_size is not None and int(cur_size) != int(size):
            raise RuntimeError(
                f"向量维度不匹配：集合 {name} 为 {cur_size}，当前模型输出 {size}。"
                f"请手动删除集合 {name} 后重建（Windows 下建议先 docker compose restart qdrant）。"
            )
        if recreate:
            clear_points(name)
        return

    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR: qm.VectorParams(size=size, distance=qm.Distance.COSINE)
        },
        sparse_vectors_config={
            SPARSE_VECTOR: qm.SparseVectorParams(modifier=qm.Modifier.IDF)
        },
    )

    payload_indexes: dict[str, qm.PayloadSchemaType] = {
        "doc_type": qm.PayloadSchemaType.KEYWORD,
        "source_file": qm.PayloadSchemaType.KEYWORD,
        "source_priority": qm.PayloadSchemaType.INTEGER,
        "source_group": qm.PayloadSchemaType.KEYWORD,
        "origin": qm.PayloadSchemaType.KEYWORD,
        "diseases": qm.PayloadSchemaType.KEYWORD,
        "formula_names": qm.PayloadSchemaType.KEYWORD,
        "acupoints": qm.PayloadSchemaType.KEYWORD,
        "case_serial": qm.PayloadSchemaType.KEYWORD,
        "chapter_no": qm.PayloadSchemaType.INTEGER,
        "doc_id": qm.PayloadSchemaType.KEYWORD,
    }
    for field, schema in payload_indexes.items():
        try:
            client.create_payload_index(name, field_name=field, field_schema=schema)
        except Exception as exc:  # noqa: BLE001
            # 本地模式不支持 payload 索引（只有警告，不影响暴力检索）
            logger.debug("create_payload_index %s skipped: %s", field, exc)


def count_points(collection: str | None = None) -> int:
    name = collection or settings.qdrant_collection
    try:
        res = get_client().count(name, exact=True)
        return int(res.count)
    except Exception:  # noqa: BLE001
        return 0


def health() -> dict[str, Any]:
    """返回 qdrant 连通性与各分组点数，供 /health 使用。"""
    try:
        client = get_client()
        groups = {
            "nihai": settings.qdrant_collection,
            "classics": settings.qdrant_collection_classics,
            "judges": settings.qdrant_collection_judges,
        }
        counts: dict[str, int] = {}
        total = 0
        for key, name in groups.items():
            if not client.collection_exists(name):
                counts[key] = 0
                continue
            n = count_points(name)
            counts[key] = n
            total += n
        return {
            "reachable": True,
            "collection": bool(counts.get("nihai")),
            "points": total,
            "group_points": counts,
        }
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "collection": False, "points": 0, "error": str(exc)}
