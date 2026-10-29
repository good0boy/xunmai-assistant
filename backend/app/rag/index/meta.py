"""知识库索引元信息读写（存 SQLite 的 kb_meta 表）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.db.models import KbMeta
from app.db.session import session_scope

# 影响索引内容的文件后缀
_CORPUS_SUFFIXES = (".md", ".txt")


def corpus_hash() -> str:
    """语料内容指纹（主语料 + 外部来源）。

    **必须进 build_version**：改了语料不重建索引，检索会静默返回旧内容，
    而 /health 还报 ready（子代理实测确认过这个洞）。指纹用文件内容而不是
    mtime —— touch 一下文件不该被判成 stale，但改了内容必须。
    """
    from app.core.config import settings
    from app.core.paths import resolve_path

    roots: list[Path] = []
    for raw in (settings.kb_root, settings.kb_external_root):
        try:
            roots.append(resolve_path(raw))
        except Exception:  # noqa: BLE001
            continue

    h = hashlib.sha1()
    for root in roots:
        if not root.is_dir():
            h.update(f"!missing:{root.name}".encode())
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _CORPUS_SUFFIXES:
                continue
            try:
                rel = p.relative_to(root).as_posix()
                h.update(f"{root.name}/{rel}|".encode())
                h.update(p.read_bytes())
            except OSError:
                continue
    return h.hexdigest()[:16]


def expected_build_version() -> str:
    """建库版本号：语料指纹 + 模型 + 切分参数 + 向量维度。

    任何一项变了都必须重建索引，否则 /health 会把旧索引当成可用的报出去。
    """
    from app.core.config import settings

    try:
        from app.rag.index.embedder import dim as embed_dim

        dim_val = embed_dim()
    except Exception:  # noqa: BLE001
        dim_val = settings.embed_dim

    raw = "|".join(
        [
            f"corpus={corpus_hash()}",
            f"model={settings.embed_model}",
            f"dim={dim_val}",
            f"target={settings.kb_target_chunk_chars}",
            f"max={settings.kb_max_chunk_chars}",
            f"min={settings.kb_min_chunk_chars}",
            f"overlap={settings.kb_overlap_chars}",
            f"prefix={settings.kb_embed_context_prefix}",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


async def read_meta(key: str) -> str | None:
    async with session_scope() as session:
        row = await session.get(KbMeta, key)
        return row.value if row else None


async def read_all_meta() -> dict[str, Any]:
    async with session_scope() as session:
        rows = (await session.execute(KbMeta.__table__.select())).all()
    return {r.key: r.value for r in rows}


async def write_meta(pairs: dict[str, Any]) -> None:
    """写元信息。**只覆盖传入的 key**，其余保持原值。

    这一点很重要：``build_index --groups classics`` 只重建经典原文，
    如果把 doc_count / external_groups 整体覆盖，会把「倪师解读 5281 块」
    写成 0、把「判语 927 块」的记录抹掉 —— 元信息开始说谎。
    """
    if not pairs:
        return
    # write=True 不能漏：session_scope 默认只读，不传就不会 commit
    async with session_scope(write=True) as session:
        for k, v in pairs.items():
            val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            await session.merge(KbMeta(key=k, value=val))
