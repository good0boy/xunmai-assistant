"""统一错误类型与异常处理器。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    code = "APP_ERROR"
    status = 400

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status


class LLMError(AppError):
    code = "LLM_ERROR"
    status = 502


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"


class KBStaleError(AppError):
    code = "KB_STALE"
    status = 503


class KBUnavailableError(AppError):
    code = "KB_UNAVAILABLE"
    status = 503


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status = 404


class ValidationError(AppError):
    code = "INVALID_INPUT"
    status = 422


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
