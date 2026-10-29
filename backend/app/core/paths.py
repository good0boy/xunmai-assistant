"""路径解析。

不能用相对 cwd 的路径：发布平台启动容器的 cwd 不一定是包根目录，
`./runtime/app.db` 很可能指到一个不存在或不可写的位置，SQLite 直接报错。

这里统一按「代码所在位置」解析，并给需要的目录做可写性探测。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# app/core/paths.py → parents[0]=core [1]=app [2]=包根
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_path(value: str | Path) -> Path:
    p = Path(value)
    # POSIX 绝对路径（/data/xxx）在 Windows 上 is_absolute() 为 False，会被误拼
    if p.is_absolute() or str(value).startswith("/"):
        return p
    return (PROJECT_ROOT / p).resolve()


def resolve_db_url(url: str) -> str:
    """把 sqlite 相对路径转成绝对路径，其他 URL 原样返回。"""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return url

    raw = url[len(prefix) :]
    if not raw or raw.startswith(":memory:"):
        return url

    # 已经是绝对路径（POSIX 的 /xxx 或带盘符）就不动
    if raw.startswith("/") or Path(raw).is_absolute():
        return url

    abs_path = (PROJECT_ROOT / raw).resolve()
    return prefix + abs_path.as_posix()


def writable_dir(preferred: str | Path, fallback_name: str) -> Path:
    """优先用指定目录；不可写则退到系统临时目录，保证服务能起来。"""
    p = resolve_path(str(preferred))
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return p
    except OSError as exc:
        fb = Path(tempfile.gettempdir()) / fallback_name
        fb.mkdir(parents=True, exist_ok=True)
        logger.warning("目录不可写 %s (%s)，改用 %s", p, exc, fb)
        return fb


def _can_write_file(p: Path) -> bool:
    try:
        with open(p, "ab"):
            pass
        return True
    except OSError:
        return False


_prepared_db: str | None = None


def prepare_db_url(url: str) -> str:
    """确保 SQLite 落在可写位置。

    镜像里的文件通常只读，而 SQLite 的 WAL 模式必须能写 -wal/-shm，
    否则会在 PRAGMA 阶段直接抛错。能写就原地用，不能写就复制到临时目录。
    """
    global _prepared_db
    if _prepared_db is not None:
        return _prepared_db

    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        _prepared_db = url
        return url

    raw = url[len(prefix) :]
    if not raw or raw.startswith(":memory:"):
        _prepared_db = url
        return url

    src = Path(raw)
    if not src.is_absolute():
        src = (PROJECT_ROOT / src).resolve()

    work = Path(tempfile.gettempdir()) / "xunmai_runtime"
    work.mkdir(parents=True, exist_ok=True)

    if src.is_file() and _can_write_file(src):
        target = src
    else:
        target = work / src.name
        if src.is_file() and not target.exists():
            import shutil

            shutil.copy2(src, target)
            logger.info("DB 复制到可写位置: %s", target)
        elif not src.is_file():
            logger.info("DB 不存在，将在 %s 新建", target)

    _prepared_db = prefix + target.as_posix()
    return _prepared_db


_prepared_index: Path | None = None


def prepare_local_index(path: str | Path) -> Path:
    """Qdrant 本地模式要写 WAL/lock，索引目录不可写时复制到临时目录。"""
    global _prepared_index
    if _prepared_index is not None:
        return _prepared_index

    src = resolve_path(path)
    if src.is_dir():
        try:
            probe = src / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            _prepared_index = src
            return src
        except OSError:
            import shutil

            dst = Path(tempfile.gettempdir()) / "xunmai_runtime" / "qdrant"
            if not (dst / "meta.json").exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                logger.info("索引复制到可写位置: %s", dst)
            _prepared_index = dst
            return dst

    src.mkdir(parents=True, exist_ok=True)
    _prepared_index = src
    return src
