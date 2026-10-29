"""访问控制。

设计原则：**默认不拦，显式配置才拦**。

- `admin_token` 为空（默认）→ 不启用鉴权，适合本机开发
- `admin_token` 非空 → 所有 /api/v1/* 需要带 `X-Admin-Token` 头
  （/health、/config 豁免，前端启动时要读 config 才知道要不要登录）

`app_passcode` 是给最终用户的入口口令，与 admin_token 分开：
管理员用 token 管接口，用户用口令进界面。
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)

# 启动时必须能访问的路径（前端要靠它们决定是否要弹登录框）
PUBLIC_PATHS = ("/api/v1/health", "/api/v1/config", "/api/v1/auth/passcode")

TOKEN_HEADER = "x-admin-token"


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED", "message": detail}})


class AdminTokenMiddleware(BaseHTTPMiddleware):
    """校验 X-Admin-Token。未配置 admin_token 时直接放行。"""

    async def dispatch(self, request: Request, call_next):
        token = (settings.admin_token or "").strip()
        if not token:
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/") or any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        provided = request.headers.get(TOKEN_HEADER, "")
        if not provided or not hmac.compare_digest(provided, token):
            logger.warning("拒绝未授权请求: %s %s", request.method, path)
            return _unauthorized("缺少或错误的访问令牌")

        return await call_next(request)


class PasscodeMiddleware(BaseHTTPMiddleware):
    """校验用户入口口令 app_passcode。未配置时直接放行。

    口令通过 `X-App-Passcode` 头传递；未携带时一律拒绝。
    """

    async def dispatch(self, request: Request, call_next):
        code = (settings.app_passcode or "").strip()
        if not code:
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/") or any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        provided = request.headers.get("x-app-passcode", "")
        if not provided or not hmac.compare_digest(provided, code):
            logger.warning("口令校验失败: %s %s", request.method, path)
            return _unauthorized("入口口令不正确")

        return await call_next(request)
