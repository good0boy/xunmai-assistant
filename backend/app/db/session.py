"""异步 SQLite 会话。WAL + busy_timeout，写操作串行化以避免锁冲突。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.paths import prepare_db_url, resolve_db_url
from app.db.migrations import run_migrations
from app.db.models import Base

# 必须在下游任何写入之前确定可写位置，镜像里目录只读会让 WAL 直接失败
engine = create_async_engine(
    prepare_db_url(settings.db_url),
    echo=False,
    future=True,
    connect_args={"check_same_thread": False, "timeout": 30},
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# SQLite 单写者，用进程内锁把写操作串起来
_write_lock = asyncio.Lock()


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@asynccontextmanager
async def session_scope(*, write: bool = False) -> AsyncIterator[AsyncSession]:
    """写操作必须显式传 write=True，否则不会 commit。

    这个默认值是刻意的：读多写少的场景下，忘记 commit 比误提交更安全，
    但也意味着写路径漏传参数会静默失败 —— 写路径请务必核对。
    """
    if write:
        async with _write_lock:
            async with SessionLocal() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
    else:
        async with SessionLocal() as session:
            yield session


async def init_db() -> None:
    # DB 所在目录可能不存在，先建出来
    raw = resolve_db_url(settings.db_url).removeprefix("sqlite+aiosqlite:///")
    if raw:
        try:
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await conn.execute(text("SELECT 1"))


async def dispose_db() -> None:
    await engine.dispose()
