"""反馈收集。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db.models import Feedback
from app.db.session import session_scope

router = APIRouter(tags=["feedback"])


class FeedbackReq(BaseModel):
    message_id: str
    rating: int = Field(ge=-1, le=1)
    reason: str | None = None


@router.post("/feedback")
async def submit(req: FeedbackReq) -> dict[str, Any]:
    async with session_scope(write=True) as s:
        s.add(Feedback(message_id=req.message_id, rating=req.rating, reason=req.reason))
    return {"ok": True}
