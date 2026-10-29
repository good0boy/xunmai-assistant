"""访问控制中间件测试。

覆盖：
- 未配置 token / 口令时全部放行（默认不拦，本机开发不受影响）
- 配置后，无凭据一律 401
- 凭据正确时放行
- /health、/config 永远公开（前端启动要读）
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import auth as auth_mod


@pytest.fixture()
def app_factory(monkeypatch):
    """每次造一个带鉴权中间件的最小 app。"""

    def _make(admin_token: str = "", app_passcode: str = "") -> FastAPI:
        from app.core.config import settings

        monkeypatch.setattr(settings, "admin_token", admin_token, raising=False)
        monkeypatch.setattr(settings, "app_passcode", app_passcode, raising=False)

        app = FastAPI()
        app.add_middleware(auth_mod.AdminTokenMiddleware)
        app.add_middleware(auth_mod.PasscodeMiddleware)

        @app.get("/api/v1/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/v1/config")
        async def config():
            return {"passcode_required": bool(settings.app_passcode)}

        @app.get("/api/v1/kb/search")
        async def search():
            return {"hits": []}

        return app

    return _make


def test_open_by_default(app_factory):
    """默认不配凭据 → 一切放行。"""
    client = TestClient(app_factory())
    assert client.get("/api/v1/kb/search").status_code == 200


def test_admin_token_required(app_factory):
    client = TestClient(app_factory(admin_token="secret-token"))
    assert client.get("/api/v1/kb/search").status_code == 401
    assert client.get("/api/v1/kb/search", headers={"X-Admin-Token": "wrong"}).status_code == 401
    ok = client.get("/api/v1/kb/search", headers={"X-Admin-Token": "secret-token"})
    assert ok.status_code == 200


def test_passcode_required(app_factory):
    client = TestClient(app_factory(app_passcode="abc123"))
    assert client.get("/api/v1/kb/search").status_code == 401
    assert client.get("/api/v1/kb/search", headers={"X-App-Passcode": "abc123"}).status_code == 200


def test_both_required(app_factory):
    client = TestClient(app_factory(admin_token="t1", app_passcode="p1"))
    # 只给一个都不行
    assert client.get("/api/v1/kb/search", headers={"X-Admin-Token": "t1"}).status_code == 401
    assert client.get("/api/v1/kb/search", headers={"X-App-Passcode": "p1"}).status_code == 401
    both = client.get(
        "/api/v1/kb/search",
        headers={"X-Admin-Token": "t1", "X-App-Passcode": "p1"},
    )
    assert both.status_code == 200


def test_public_paths_always_open(app_factory):
    """前端启动时要读 health 和 config 才能决定是否弹登录框，不能拦。"""
    client = TestClient(app_factory(admin_token="t1", app_passcode="p1"))
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/config").status_code == 200
