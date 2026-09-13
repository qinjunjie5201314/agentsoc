"""FastAPI 端点测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    """健康检查返回 200。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "AgentSoc"
    assert "version" in data


def test_root():
    """根路径返回服务信息。"""
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "service" in data
    assert "docs" in data


def test_info():
    """v1/info 返回元信息。"""
    resp = client.get("/v1/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "env" in data
    assert "classifier_mode" in data


def test_docs_available():
    """OpenAPI 文档可访问。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "AgentSoc"
