"""会话与消息持久化。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text

from app.db.models import Conversation, Image, Message, MessageCitation, now_iso
from app.db.session import session_scope


def _new_id() -> str:
    return uuid.uuid4().hex[:24]


async def create_conversation(title: str = "新对话", model: str | None = None) -> dict[str, Any]:
    cid = _new_id()
    async with session_scope(write=True) as s:
        conv = Conversation(id=cid, title=title[:200], model=model, meta_json="{}")
        s.add(conv)
    return {"id": cid, "title": title, "created_at": now_iso()}


async def get_conversation(cid: str) -> dict[str, Any] | None:
    async with session_scope() as s:
        conv = await s.get(Conversation, cid)
        if not conv or conv.archived:
            return None
        return {
            "id": conv.id,
            "title": conv.title,
            "summary": conv.summary,
            "meta": json.loads(conv.meta_json or "{}"),
            "model": conv.model,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }


async def list_conversations(limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
    async with session_scope() as s:
        stmt = (
            select(Conversation)
            .where(Conversation.archived == 0)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await s.execute(stmt)).scalars().all()

        out: list[dict[str, Any]] = []
        for c in rows:
            last = (
                await s.execute(
                    select(Message)
                    .where(Message.conversation_id == c.id)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            out.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "updated_at": c.updated_at,
                    "preview": (last.content[:60] if last else ""),
                }
            )
        return out


async def update_conversation(cid: str, *, title: str | None = None, archived: bool | None = None) -> bool:
    async with session_scope(write=True) as s:
        conv = await s.get(Conversation, cid)
        if not conv:
            return False
        if title is not None:
            conv.title = title[:200]
        if archived is not None:
            conv.archived = 1 if archived else 0
        conv.updated_at = now_iso()
        return True


async def delete_conversation(cid: str, *, hard: bool = False) -> bool:
    async with session_scope(write=True) as s:
        conv = await s.get(Conversation, cid)
        if not conv:
            return False
        if hard:
            await s.delete(conv)
        else:
            conv.archived = 1
            conv.updated_at = now_iso()
        return True


async def add_message(
    cid: str,
    role: str,
    content: str,
    *,
    status: str = "done",
    image_ids: list[str] | None = None,
    trace: dict[str, Any] | None = None,
) -> str:
    mid = _new_id()
    async with session_scope(write=True) as s:
        msg = Message(
            id=mid,
            conversation_id=cid,
            role=role,
            content=content,
            status=status,
            image_ids=json.dumps(image_ids, ensure_ascii=False) if image_ids else None,
            trace_json=json.dumps(trace, ensure_ascii=False) if trace else None,
        )
        s.add(msg)
        conv = await s.get(Conversation, cid)
        if conv:
            conv.updated_at = now_iso()
            if conv.title in ("新对话", "") and role == "user" and content.strip():
                conv.title = _auto_title(content)
    return mid


def _auto_title(text: str) -> str:
    t = " ".join(text.split())
    return t[:24] + ("…" if len(t) > 24 else "")


async def finish_message(
    mid: str,
    *,
    status: str = "done",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cache_hit: int | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    async with session_scope(write=True) as s:
        msg = await s.get(Message, mid)
        if not msg:
            return
        msg.status = status
        msg.tokens_in = tokens_in
        msg.tokens_out = tokens_out
        msg.cache_hit_tokens = cache_hit
        msg.latency_ms = latency_ms
        if error_code:
            msg.error_code = error_code


async def append_message_content(mid: str, delta: str) -> None:
    """流式过程中追加内容（用于中断后保留已生成部分）。"""
    async with session_scope(write=True) as s:
        msg = await s.get(Message, mid)
        if msg:
            msg.content = (msg.content or "") + delta
            msg.status = "streaming"


async def save_citations(mid: str, citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    async with session_scope(write=True) as s:
        # 先删旧引用再插：表上有 (message_id, n) 唯一约束，
        # 重试路径直接插会撞约束报错
        await s.execute(
            sa_delete(MessageCitation).where(MessageCitation.message_id == mid)
        )
        for c in citations:
            cites = c.get("judge_cites") or []
            s.add(
                MessageCitation(
                    message_id=mid,
                    n=int(c.get("n", 0)),
                    chunk_id=str(c.get("chunk_id", "")),
                    doc_id=c.get("doc_id"),
                    source_file=c.get("source_file"),
                    section_path=c.get("section_path"),
                    snippet=c.get("snippet"),
                    score=float(c["score"]) if c.get("score") is not None else None,
                    rank=int(c["rank"]) if c.get("rank") is not None else None,
                    source_group=c.get("source_group"),
                    origin=c.get("origin"),
                    judge_cites_json=(
                        json.dumps(cites, ensure_ascii=False) if cites else None
                    ),
                )
            )


async def get_messages(cid: str, limit: int = 200) -> list[dict[str, Any]]:
    """取会话消息，按时间升序返回。

    排序用 (created_at, rowid)：rowid 是 SQLite 的隐式插入序号，等于真实写入顺序。
    **不能用随机的 uuid 做 tiebreaker** —— 同秒写入的两条消息（user/assistant）
    顺序会变成随机，直接污染 LLM 的历史上下文。
    必须先按 created_at DESC 取最近 limit 条再反转：直接 ASC + LIMIT
    拿到的是最旧的 limit 条，会话消息数超过 limit 之后，
    新的对话内容永远进不了上下文。
    """
    async with session_scope() as s:
        stmt = (
            select(Message)
            .where(Message.conversation_id == cid)
            .order_by(Message.created_at.desc(), text("rowid DESC"))
            .limit(limit)
        )
        rows = list((await s.execute(stmt)).scalars().all())
        rows.reverse()
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "status": m.status,
                "image_ids": json.loads(m.image_ids) if m.image_ids else [],
                "created_at": m.created_at,
            }
            for m in rows
        ]


async def get_history_for_prompt(cid: str, limit: int = 8) -> list[dict[str, str]]:
    """取最近若干轮作为对话历史。"""
    msgs = await get_messages(cid, limit=200)
    usable = [m for m in msgs if m["status"] in ("done", "partial") and m["content"].strip()]
    return [{"role": m["role"], "content": m["content"]} for m in usable[-limit:]]


async def get_citations(mid: str) -> list[dict[str, Any]]:
    async with session_scope() as s:
        stmt = (
            select(MessageCitation)
            .where(MessageCitation.message_id == mid)
            .order_by(MessageCitation.n.asc())
        )
        rows = (await s.execute(stmt)).scalars().all()
        return [
            {
                "n": r.n,
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "source_file": r.source_file,
                "section_path": r.section_path,
                "snippet": r.snippet,
                "score": r.score,
                # 这三个字段必须从库里带出来 —— 否则页面刷新后
                # 「倪师解读 / 经典原文」标签与「B 站原话出处」链接会整块消失
                "source_group": r.source_group or "nihai",
                "origin": r.origin or "",
                "judge_cites": (
                    json.loads(r.judge_cites_json) if r.judge_cites_json else []
                ),
            }
            for r in rows
        ]


async def get_images(ids: list[str]) -> list[Image]:
    if not ids:
        return []
    async with session_scope() as s:
        stmt = select(Image).where(Image.id.in_(ids))
        return list((await s.execute(stmt)).scalars().all())


async def update_image_vision(image_id: str, vision: dict[str, Any], status: str) -> None:
    async with session_scope(write=True) as s:
        img = await s.get(Image, image_id)
        if img:
            img.vision_json = json.dumps(vision, ensure_ascii=False)
            img.vision_status = status
