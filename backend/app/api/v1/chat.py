"""SSE 对话端点。

用 POST + fetch 读流（不用 EventSource，它不支持 POST 和自定义 Header）。
所有 data 都是单行 JSON，避免多行解析歧义。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

PING_INTERVAL = 15.0


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    content: str = Field(default="", max_length=4000)
    image_ids: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _wrapped(req: ChatRequest) -> AsyncIterator[str]:
    """在事件流中穿插心跳，防止中间层掐断空闲连接。"""
    gen = chat_service.stream_chat(
        conversation_id=req.conversation_id,
        content=req.content.strip(),
        image_ids=req.image_ids or None,
        with_citations=bool(req.options.get("with_citations", True)),
    )

    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in gen:
                await queue.put(item)
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat 流异常")
            await queue.put(("error", {"code": "INTERNAL", "message": str(exc)[:200]}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            event, payload = item
            yield _sse(event, payload)
    finally:
        if not task.done():
            task.cancel()
        try:
            await gen.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    if not req.content and not req.image_ids:
        from app.core.errors import ValidationError

        raise ValidationError("消息内容不能为空")

    return StreamingResponse(
        _wrapped(req),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
