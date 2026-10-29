"""数据库表定义。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now_iso() -> str:
    """时间戳。

    必须到**微秒**精度：一轮对话里 user 与 assistant 两条消息几乎必然落在
    同一毫秒内，秒级精度会让它们的先后变成随机（子代理实测：线上 3/8 个会话
    出现 assistant 排在 user 之前，直接污染 LLM 的历史上下文）。
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_up_to_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    archived: Mapped[int] = mapped_column(Integer, default=0)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="done")
    image_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_msg_conv", "conversation_id", "created_at"),)


class MessageCitation(Base):
    __tablename__ = "message_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    n: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(200), nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 来源分组与判语出处 —— 不存的话，页面刷新后读库渲染就会丢掉
    # 「倪师解读 / 经典原文」标签与「B 站原话出处」链接（前端只从 SSE 拿过一次）
    source_group: Mapped[str | None] = mapped_column(String(16), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_cites_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    message: Mapped[Message] = relationship(back_populates="citations")

    __table_args__ = (
        Index("idx_cit_msg", "message_id", "n"),
        UniqueConstraint("message_id", "n", name="uq_cit_msg_n"),
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime: Mapped[str] = mapped_column(String(48))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    vision_status: Mapped[str] = mapped_column(String(16), default="disabled")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    expires_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class KbMeta(Base):
    __tablename__ = "kb_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
