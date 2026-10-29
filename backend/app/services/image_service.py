"""图片处理：压缩、剥 EXIF、去重、可选的 VLM 分析。

两阶段设计的原因：DeepSeek 无视觉能力，直连多模态会把主链路绑死在某一家 VLM 上。
先让 VLM 出结构化描述，再喂给主模型，好处是描述可缓存、可展示、可被用户纠正。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image as PILImage
from PIL import ImageOps

from app.core.config import settings
from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

VISION_SCHEMA_PROMPT = """你是中医望诊图像分析助手。分析这张舌象/面部照片，按以下 JSON 输出。
看不清的项填 "unknown"，**不要推测**。

{
  "tongue_body_color": "淡白|淡红|红|绛|紫暗|unknown",
  "tongue_body_shape": "正常|胖大|瘦薄|齿痕|裂纹|unknown",
  "coating_color": "薄白|白|黄|灰黑|剥落|unknown",
  "coating_texture": "薄|厚|腻|腐|少苔|无苔|unknown",
  "moisture": "润|燥|滑|unknown",
  "face_color": "正常|苍白|萎黄|潮红|晦暗|青紫|unknown",
  "lip_color": "淡|红|紫暗|unknown",
  "notable_findings": ["明显的异常，没有就给空数组"],
  "confidence": "low|medium|high",
  "unclear": ["哪些项因为光线/角度/清晰度判断不了"]
}

只输出 JSON，不要任何解释。"""


def _upload_root() -> Path:
    from app.core.paths import writable_dir

    return writable_dir(settings.upload_dir, "xunmai_uploads")


def process_image(raw: bytes, mime: str) -> dict[str, Any]:
    """校验、旋转、缩放、剥 EXIF、转 JPEG。返回处理结果。"""
    if mime not in ALLOWED_MIME:
        raise ValidationError(f"不支持的图片格式：{mime}，仅支持 jpg/png/webp")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise ValidationError(f"图片超过 {settings.max_upload_mb}MB")

    try:
        img = PILImage.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)  # 按 EXIF 方向摆正
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("图片无法解析") from exc

    # 转 RGB 并丢弃所有 EXIF（含 GPS），隐私必须
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    edge = settings.image_max_edge
    if max(img.size) > edge:
        img.thumbnail((edge, edge), PILImage.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    data = buf.getvalue()

    return {
        "data": data,
        "mime": "image/jpeg",
        "width": img.width,
        "height": img.height,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def store_image(processed: dict[str, Any]) -> tuple[str, str]:
    """落盘，返回 (image_id, 相对上传根目录的路径)。"""
    image_id = uuid.uuid4().hex[:24]
    month = time.strftime("%Y%m")
    root = _upload_root()

    rel = f"{month}/{image_id}.jpg"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(processed["data"])
    logger.info("image stored: %s (%s bytes)", rel, processed["size"])
    return image_id, rel


def abs_path(stored: str) -> Path:
    p = Path(stored)
    if p.is_absolute():
        return p
    return _upload_root() / p


async def analyze_one(image_bytes: bytes) -> dict[str, Any] | None:
    """调 VLM 出结构化描述。未配置 VLM 时返回 None。"""
    if not settings.vision_enabled:
        return None

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": settings.vlm_model,
        "messages": [
            {"role": "system", "content": VISION_SCHEMA_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": "请分析这张照片。"},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
    }

    from app.llm.client import join_url

    url = join_url(settings.vlm_base_url, "/chat/completions")

    try:
        async with httpx.AsyncClient(timeout=settings.vlm_timeout_s) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.vlm_api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code >= 400:
                logger.warning("VLM 返回 %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            raw = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("VLM 返回非 JSON")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("VLM 调用失败: %s", exc)
        return None

    confidence = str(parsed.get("confidence", "medium")).lower()
    note = ""
    if confidence == "low":
        note = "光线或角度不佳"
    return {"data": parsed, "confidence": confidence, "confidence_note": note}


async def analyze_images(images: list[Any]) -> dict[str, Any] | None:
    """对第一张可用图片做分析。多图时只取第一张，避免成本失控。"""
    if not settings.vision_enabled or not images:
        return None

    img = images[0]
    path = abs_path(img.path)
    if not path.exists():
        return None

    result = await analyze_one(path.read_bytes())
    if result:
        from app.services.conversation_service import update_image_vision

        await update_image_vision(img.id, result["data"], "ok")
    return result
