"""系统端点：健康检查与前端运行时配置。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.rag.index import qdrant_store

router = APIRouter(tags=["system"])

SCHEMA_VERSION = 1


def _kb_status(build_version: str | None, points: int) -> str:
    if not points:
        return "missing"
    if not build_version:
        return "stale"
    from app.rag.index.meta import expected_build_version

    return "ready" if build_version == expected_build_version() else "stale"


@router.get("/health")
async def health() -> dict[str, Any]:
    q = qdrant_store.health()
    build_version = None
    if q.get("collection"):
        try:
            from app.rag.index.meta import read_meta

            build_version = await read_meta("build_version")
        except Exception:  # noqa: BLE001
            build_version = None

    status = _kb_status(build_version, int(q.get("points") or 0))
    if not q.get("reachable"):
        status = "unavailable"

    from app.rag.index.embedder import dense_available

    return {
        "status": "ok",
        "app_env": settings.app_env,
        "kb_status": status,
        "kb_points": int(q.get("points") or 0),
        "build_version": build_version,
        "qdrant_reachable": bool(q.get("reachable")),
        "qdrant_mode": settings.qdrant_mode,
        "dense_available": dense_available(),
        "llm_configured": settings.llm_configured,
        "vision_enabled": settings.vision_enabled,
        "schema_version": SCHEMA_VERSION,
    }


@router.get("/config")
async def public_config() -> dict[str, Any]:
    """前端启动时拉一次，决定是否显示图片入口等。"""
    return {
        "vision_enabled": settings.vision_enabled,
        "passcode_required": bool(settings.app_passcode),
        "token_required": bool(settings.admin_token),
        "model": settings.llm_model,
        "disclaimer_mode": settings.disclaimer_mode,
        "red_flag_enabled": settings.red_flag_enabled,
        "max_upload_mb": settings.max_upload_mb,
        "app_title": "寻脉问诊助手",
        "app_subtitle": "中医古籍与医案学习参考",
    }


@router.get("/stream-probe", include_in_schema=False)
async def stream_probe() -> StreamingResponse:
    """流式探针：每 0.5 秒推一条带服务端时间戳的事件，共 15 条。

    用来判断「流式不生效」到底出在哪一层：
    浏览器打开这个地址，如果逐条出现 → 链路上的代理没有缓冲；
    如果 7.5 秒后一次性全出来 → 中间有代理在缓冲响应体。
    与业务数据无关，纯诊断用。
    """

    async def gen():
        t0 = time.perf_counter()
        for i in range(15):
            payload = {
                "i": i,
                "server_elapsed_s": round(time.perf_counter() - t0, 3),
                "wall_ts": time.time(),
            }
            yield f"event: tick\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
