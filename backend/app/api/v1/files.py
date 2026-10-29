"""图片上传与读取。"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models import Image
from app.db.session import session_scope
from app.services import image_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


@router.post("/files/images")
async def upload_image(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    if not raw:
        raise ValidationError("空文件")

    processed = image_service.process_image(raw, file.content_type or "image/jpeg")

    # 秒传：同一份图片直接复用
    async with session_scope() as s:
        from sqlalchemy import select

        existing = (
            await s.execute(select(Image).where(Image.sha256 == processed["sha256"]))
        ).scalar_one_or_none()
        if existing:
            return _image_payload(existing)

    image_id, stored = image_service.store_image(processed)
    expires = (datetime.now(UTC) + timedelta(days=settings.image_retention_days)).isoformat(
        timespec="seconds"
    )

    async with session_scope(write=True) as s:
        img = Image(
            id=image_id,
            path=stored,
            sha256=processed["sha256"],
            mime=processed["mime"],
            width=processed["width"],
            height=processed["height"],
            size_bytes=processed["size"],
            vision_status="disabled" if not settings.vision_enabled else "pending",
            expires_at=expires,
        )
        s.add(img)
        s.flush()
        payload = _image_payload(img)

    return payload


def _image_payload(img: Image) -> dict[str, Any]:
    return {
        "image_id": img.id,
        "url": f"/api/v1/files/{img.id}",
        "width": img.width,
        "height": img.height,
        "size": img.size_bytes,
        "vision": {
            "available": settings.vision_enabled,
            "status": img.vision_status,
        },
    }


@router.get("/files/{image_id}")
async def get_image(image_id: str) -> FileResponse:
    async with session_scope() as s:
        img = await s.get(Image, image_id)
        if not img:
            raise NotFoundError("图片不存在")
        path = image_service.abs_path(img.path)

    if not path.exists():
        raise NotFoundError("图片文件已清理")

    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
