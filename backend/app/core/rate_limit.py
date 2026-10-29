"""简易内存限流。单机部署够用，多实例应换 Redis。

只对写操作和对话接口生效，静态资源和健康检查不限。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings

# 这些路径不参与限流（注意不要放 "/"，那会让所有路径都豁免）
_EXEMPT_PREFIXES = ("/api/v1/health", "/api/v1/config", "/api/docs", "/api/openapi.json")
_EXEMPT_EXACT = {"/"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int | None = None) -> None:
        super().__init__(app)
        self.limit = per_minute or settings.rate_limit_per_min
        self.window = 60.0
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if self.limit <= 0 or request.method == "OPTIONS":
            return await call_next(request)
        if path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        bucket = self.hits[key]

        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry = int(self.window - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": f"请求过于频繁，请 {retry} 秒后再试",
                    }
                },
                headers={"Retry-After": str(retry)},
            )

        bucket.append(now)

        # 顺手清理过期 key，避免长时间运行内存膨胀
        if len(self.hits) > 2048:
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                self.hits.pop(k, None)

        return await call_next(request)
