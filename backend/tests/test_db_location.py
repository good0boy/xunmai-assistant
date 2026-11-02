"""SQLite 落盘位置必须跨容器一致。

回归场景（2026-11-02 服务器部署实测）：首次部署时 data/app.db 根本不存在，
旧逻辑把它复制到容器内 /tmp/xunmai_runtime/ —— **每个容器各有一份独立副本**，
于是：
- 一次性容器（sync_kb_meta）写入的 kb_meta，服务容器永远读不到
  → /health 报 kb_status=stale、build_version=null
- 容器一重启，会话数据全部消失

所以「文件不存在但父目录可写」必须原地新建，不能复制到临时目录。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import paths  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    """prepare_db_url 有进程内缓存，每个用例都要清。"""
    paths._prepared_db = None
    yield
    paths._prepared_db = None


def test_missing_file_in_writable_dir_stays_in_place(tmp_path):
    """文件不存在 + 目录可写 → 原地创建（首次部署的常态）。"""
    target = tmp_path / "app.db"
    url = paths.prepare_db_url(f"sqlite+aiosqlite:///{target.as_posix()}")
    assert url == f"sqlite+aiosqlite:///{target.as_posix()}", (
        "首次部署时 app.db 不存在，不能复制到临时目录（每个容器会各有一份）"
    )


def test_existing_writable_file_stays_in_place(tmp_path):
    target = tmp_path / "app.db"
    target.write_text("", encoding="utf-8")
    url = paths.prepare_db_url(f"sqlite+aiosqlite:///{target.as_posix()}")
    assert url == f"sqlite+aiosqlite:///{target.as_posix()}"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ACL 语义与 chmod 不同")
def test_unwritable_file_falls_back_to_tmp(tmp_path):
    """文件存在但不可写 → 兜底复制到临时目录（镜像只读场景）。"""
    target = tmp_path / "app.db"
    target.write_text("", encoding="utf-8")
    target.chmod(0o444)
    url = paths.prepare_db_url(f"sqlite+aiosqlite:///{target.as_posix()}")
    assert "xunmai_runtime" in url, "不可写时应回退到临时目录"
