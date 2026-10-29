"""会话 CRUD 与消息查询。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.errors import NotFoundError
from app.services import conversation_service as conv

router = APIRouter(tags=["conversations"])


class CreateReq(BaseModel):
    title: str = "新对话"


class PatchReq(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.post("/conversations")
async def create(req: CreateReq) -> dict[str, Any]:
    return await conv.create_conversation(title=req.title)


@router.get("/conversations")
async def list_all(
    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    items = await conv.list_conversations(limit=limit, offset=offset)
    return {"items": items, "count": len(items)}


@router.get("/conversations/{cid}")
async def detail(cid: str) -> dict[str, Any]:
    info = await conv.get_conversation(cid)
    if not info:
        raise NotFoundError("会话不存在")
    info["messages"] = await conv.get_messages(cid)
    return info


@router.patch("/conversations/{cid}")
async def patch(cid: str, req: PatchReq) -> dict[str, bool]:
    ok = await conv.update_conversation(cid, title=req.title, archived=req.archived)
    if not ok:
        raise NotFoundError("会话不存在")
    return {"ok": True}


@router.delete("/conversations/{cid}")
async def remove(cid: str, hard: bool = Query(False)) -> dict[str, bool]:
    ok = await conv.delete_conversation(cid, hard=hard)
    if not ok:
        raise NotFoundError("会话不存在")
    return {"ok": True}


@router.get("/messages/{mid}/citations")
async def citations(mid: str) -> dict[str, Any]:
    items = await conv.get_citations(mid)
    return {"items": items, "count": len(items)}
