"""本地 ONNX 向量化。不依赖 torch，镜像体积可控。"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_model = None
_load_failed = False
_failed_at = 0.0

# 模型加载失败后的冷却期（秒）。过了这个时间自动重试 ——
# 离线集群第一次拉模型失败就永久禁用 dense 是不合理的：
# 网络恢复后应该能自动回到语义检索，而不是要重启进程。
LOAD_RETRY_COOLDOWN = 300.0


def mark_dense_unavailable(reason: str) -> None:
    """标记向量通道暂时不可用，走关键词检索。冷却期后会自动重试。"""
    global _load_failed, _failed_at
    if not _load_failed:
        logger.error(
            "向量模型不可用，暂退化为关键词检索（%.0f 秒后重试）: %s",
            LOAD_RETRY_COOLDOWN,
            reason,
        )
    _load_failed = True
    _failed_at = time.time()


def dense_available() -> bool:
    """向量通道是否可用。冷却期结束后自动恢复尝试。"""
    global _load_failed
    if _load_failed and time.time() - _failed_at >= LOAD_RETRY_COOLDOWN:
        logger.info("向量模型冷却期结束，重新尝试启用")
        _load_failed = False
    return not _load_failed


def _ensure_cache_dir() -> Path:
    from app.core.paths import writable_dir

    # huggingface_hub 认 HF_ENDPOINT 环境变量；直连 huggingface.co 在国内基本必超时
    if settings.hf_endpoint:
        import os

        os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)

    return writable_dir(settings.fastembed_cache_path, "xunmai_models")


def get_model():
    global _model, _load_failed
    if _model is None:
        if _load_failed:
            # 冷却期没过就继续用关键词检索；过了就允许重新尝试加载
            if time.time() - _failed_at < LOAD_RETRY_COOLDOWN:
                raise RuntimeError("向量模型不可用（此前加载失败，冷却中）")
            logger.info("向量模型冷却期结束，重新尝试加载")
            _load_failed = False
        from fastembed import TextEmbedding

        logger.info("loading embedding model %s", settings.embed_model)
        _model = TextEmbedding(
            model_name=settings.embed_model,
            cache_dir=str(_ensure_cache_dir()),
            threads=settings.embed_threads,
        )
        logger.info("embedding model ready")
    return _model


def embed_documents(texts: Iterable[str], batch_size: int | None = None) -> list[list[float]]:
    model = get_model()
    bs = batch_size or settings.embed_batch
    out: list[list[float]] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        for vec in model.embed(buf, batch_size=bs):
            out.append(_to_list(vec))
        buf.clear()

    for t in texts:
        buf.append(t)
        if len(buf) >= 256:
            flush()
    flush()
    return out


def embed_query(text: str, *, with_prefix: bool = True) -> list[float]:
    """查询侧加 bge 官方推荐的 instruction 前缀。文档侧不加。

    带 LRU 缓存：一次问答会对同一查询做多路召回（多个变体各编一次），
    重复编码是纯浪费的 ONNX 推理（单次约 10~30ms）。
    """
    return list(_embed_query_cached(text, with_prefix, settings.embed_query_prefix))


@lru_cache(maxsize=512)
def _embed_query_cached(text: str, with_prefix: bool, prefix: str) -> tuple[float, ...]:
    model = get_model()
    q = f"{prefix}{text}" if with_prefix else text

    # 不同 fastembed 版本对 query_embed 的支持不一致，做兼容
    if hasattr(model, "query_embed"):
        try:
            return tuple(_to_list(next(iter(model.query_embed(q)))))
        except Exception as exc:  # noqa: BLE001
            logger.warning("query_embed failed, fallback to embed: %s", exc)

    return tuple(_to_list(next(iter(model.embed([q])))))


def _to_list(vec) -> list[float]:
    if hasattr(vec, "tolist"):
        return [float(x) for x in vec.tolist()]
    return [float(x) for x in vec]


_dim: int | None = None


def dim() -> int:
    """探测实际输出维度。

    fastembed 会把模型名映射到自家的 ONNX 优化版，维度可能和原模型声明不一致
    （实测 BAAI/bge-small-zh-v1.5 输出 512 而非 384）。建集合前必须实测，不能猜。
    """
    global _dim
    if _dim is None:
        _dim = len(embed_query("维度探针", with_prefix=False))
        logger.info("embedding dim detected: %s", _dim)
    return _dim
