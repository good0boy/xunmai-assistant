"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.v1 import chat, conversations, feedback, files, kb, system
from app.core.auth import AdminTokenMiddleware, PasscodeMiddleware
from app.core.config import settings
from app.core.errors import NotFoundError, install_exception_handlers
from app.core.logging import setup_logging
from app.core.paths import resolve_path
from app.core.rate_limit import RateLimitMiddleware
from app.db.session import dispose_db, init_db
from app.rag.index import qdrant_store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging(settings.log_level)
    await init_db()
    h = qdrant_store.health()
    if h.get("reachable"):
        logger.info("qdrant ok, points=%s", h.get("points"))
    else:
        logger.warning("qdrant unreachable: %s", h.get("error"))
    logger.info("llm configured=%s vision=%s", settings.llm_configured, settings.vision_enabled)

    # 预热：jieba 词典和 ONNX 模型都在请求路径上做同步 CPU 工作，
    # 放在 lifespan 里加载，避免首个请求阻塞事件循环几百毫秒到数秒。
    _ensure_model_cache()
    await asyncio.to_thread(_warmup)
    yield
    await dispose_db()


def _ensure_model_cache() -> None:
    """把随包发布的 embedding 模型拷进 fastembed 缓存目录。

    部署时语料目录是只读挂载（/kb:ro），而 fastembed/HuggingFace 缓存需要
    可写目录（要建 .locks），所以首次启动时复制一份到可写位置。
    已经有模型就跳过（不重复拷 94MB）。
    """
    from app.core.paths import resolve_path

    bundled = resolve_path(settings.kb_models_dir)
    if not bundled.is_dir():
        return
    try:
        cache = Path(settings.fastembed_cache_path)
        if any(cache.glob("models--*")):
            return
        cache.mkdir(parents=True, exist_ok=True)
        logger.info("拷贝随包 embedding 模型: %s → %s", bundled, cache)
        shutil.copytree(bundled, cache, dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        # 拷不成不算致命：还能从 HF 下载（要能出网）
        logger.warning("随包模型拷贝失败（将尝试在线下载）: %s", exc)


def _warmup() -> None:
    try:
        from app.rag.extract.tokenize import init_jieba

        init_jieba()
        logger.info("jieba warmed up")
    except Exception as exc:  # noqa: BLE001
        logger.warning("jieba 预热失败: %s", exc)

    try:
        from app.rag.index.embedder import get_model

        get_model()
        logger.info("embedding model warmed up")
    except Exception as exc:  # noqa: BLE001
        # 模型拉不下来（离线集群）不算致命：检索会降级为纯关键词
        logger.warning("embedding 模型预热失败（检索将降级为关键词）: %s", exc)


app = FastAPI(
    title="寻脉问诊助手",
    description="中医古籍与医案学习参考",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_env == "dev" else None,
    openapi_url="/api/openapi.json" if settings.app_env == "dev" else None,
)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(AdminTokenMiddleware)
app.add_middleware(PasscodeMiddleware)

# CORS 必须放在最外层（Starlette 中后添加的中间件更外层）：
# 这样鉴权/限流的拒绝响应也带 CORS 头，否则浏览器只会看到 CORS 错误而看不到 401。
_origins = settings.cors_origin_list
_allow_credentials = True
if "*" in _origins:
    # Starlette 在 allow_all_origins + allow_credentials 时会回显请求 Origin，
    # 等于允许任意站点带凭证跨域访问。通配时必须关掉凭证传递。
    _allow_credentials = False
    logger.warning("CORS_ORIGINS 含 '*'，已关闭跨域凭证传递")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_exception_handlers(app)

app.include_router(system.router, prefix="/api/v1")
app.include_router(kb.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> object:
    # 单容器模式下根路径要交给前端，否则用户打开域名看到的是 JSON
    if settings.frontend_dist:
        index = resolve_path(settings.frontend_dist) / "index.html"
        if index.is_file():
            return FileResponse(index)
    return {"service": "xunmai-assistant", "docs": "/api/docs"}


def _mount_frontend(application: FastAPI) -> None:
    """单容器模式下由后端直接托管前端 dist（SPA 需要路由回退）。

    必须挂在所有 API 路由之后，否则会吃掉 /api/* 的请求。
    """
    if not settings.frontend_dist:
        return

    dist = resolve_path(settings.frontend_dist)
    if not dist.is_dir() or not (dist / "index.html").is_file():
        logger.warning("frontend_dist 无效，跳过静态托管: %s", dist)
        return

    logger.info("serving frontend from %s", dist)

    @application.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "docs/", "redoc")):
            raise NotFoundError("接口不存在")
        candidate = (dist / full_path).resolve()
        # 防目录穿越
        if full_path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


_mount_frontend(app)
