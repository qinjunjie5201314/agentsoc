"""安全自证 - 管理端点鉴权测试。"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.security import require_admin


@pytest.fixture
def app_client(monkeypatch):
    """构造一个带鉴权的最小 app，用 monkeypatch 控制 api_key。"""
    app = FastAPI()

    @app.get("/admin")
    async def admin(dep=Depends(require_admin)):
        return {"ok": True}

    return app


def test_no_auth_when_disabled(monkeypatch):
    """未设 API_KEY 时，管理端点开放。"""
    monkeypatch.setattr(settings, "api_key", "")
    app = FastAPI()

    @app.get("/admin")
    async def admin(dep=Depends(require_admin)):
        return {"ok": True}

    with TestClient(app) as c:
        r = c.get("/admin")
        assert r.status_code == 200


def test_auth_required_when_enabled(monkeypatch):
    """设了 API_KEY 时，无头/错 key 都 401。"""
    monkeypatch.setattr(settings, "api_key", "secret123")
    app = FastAPI()

    @app.get("/admin")
    async def admin(dep=Depends(require_admin)):
        return {"ok": True}

    with TestClient(app) as c:
        # 无 key → 401
        assert c.get("/admin").status_code == 401
        # 错 key → 401
        assert c.get("/admin", headers={"X-API-Key": "wrong"}).status_code == 401
        # 正确 key → 200
        assert c.get("/admin", headers={"X-API-Key": "secret123"}).status_code == 200


def test_admin_auth_enabled_property(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    assert settings.admin_auth_enabled is False
    monkeypatch.setattr(settings, "api_key", "x")
    assert settings.admin_auth_enabled is True
