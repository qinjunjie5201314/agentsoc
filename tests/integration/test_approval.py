"""D2 合规能力 · 策略变更审批 - 单元/集成测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Base
from app.policy import PolicyRegistry, create_approval_router
from app.policy.change_log import ChangeStatus, PolicyChange


@pytest.fixture()
def engine(tmp_path: Path):
    eng = sa.create_engine(
        f"sqlite:///{tmp_path / 'approval_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def factory(engine):
    def _factory():
        return sa.orm.Session(engine, expire_on_commit=False)
    return _factory


@pytest.fixture()
def policy_dir(tmp_path: Path):
    (tmp_path / "empty.yaml").write_text("rules: []\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(engine, factory, policy_dir) -> TestClient:
    registry = PolicyRegistry(policy_dir)
    registry.reload(trigger="startup")

    app = FastAPI()

    def _db_dep():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.include_router(create_approval_router(registry=registry, db_dep=_db_dep))
    return TestClient(app)


class TestChangeProposal:
    def test_submit_and_list(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "禁用 get_weather", "proposed_by": "alice"})
        assert r.status_code == 200
        body = r.json()
        assert body["change"]["status"] == "pending"
        assert body["change"]["title"] == "禁用 get_weather"

        r2 = client.get("/v1/policy/change")
        assert r2.json()["total"] == 1

    def test_get_single(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "t1"})
        cid = r.json()["change"]["id"]
        r2 = client.get(f"/v1/policy/change/{cid}")
        assert r2.status_code == 200
        assert r2.json()["title"] == "t1"

    def test_get_404(self, client: TestClient) -> None:
        assert client.get("/v1/policy/change/9999").status_code == 404


class TestApprove:
    def test_approve_with_yaml(self, client: TestClient, policy_dir: Path) -> None:
        r = client.post("/v1/policy/change", json={
            "title": "加规则",
            "yaml_snippet": "rules:\n- id: test_rule\n  description: t\n  severity: low\n  patterns:\n  - type: keyword_any\n    value: ['foo']\n",
            "proposed_by": "alice",
        })
        cid = r.json()["change"]["id"]

        r2 = client.post(f"/v1/policy/change/{cid}/approve", json={"reviewed_by": "bob"})
        assert r2.status_code == 200
        body = r2.json()
        assert body["change"]["status"] == "approved"
        assert body["change"]["reviewed_by"] == "bob"
        # 有 yaml 片段，reload 应发生，关联 policy_revision
        assert body["change"]["policy_revision"] is not None

    def test_approve_without_yaml(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "仅记录，不改策略"})
        cid = r.json()["change"]["id"]
        r2 = client.post(f"/v1/policy/change/{cid}/approve", json={})
        assert r2.json()["change"]["status"] == "approved"
        # 无 yaml，无 reload
        assert r2.json()["reload"] is None

    def test_approve_twice_conflict(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "t"})
        cid = r.json()["change"]["id"]
        client.post(f"/v1/policy/change/{cid}/approve", json={})
        r2 = client.post(f"/v1/policy/change/{cid}/approve", json={})
        assert r2.status_code == 409


class TestReject:
    def test_reject(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "t", "proposed_by": "alice"})
        cid = r.json()["change"]["id"]
        r2 = client.post(f"/v1/policy/change/{cid}/reject", json={"reviewed_by": "bob", "review_note": "不合理"})
        assert r2.json()["change"]["status"] == "rejected"
        assert r2.json()["change"]["review_note"] == "不合理"

    def test_reject_then_approve_conflict(self, client: TestClient) -> None:
        r = client.post("/v1/policy/change", json={"title": "t"})
        cid = r.json()["change"]["id"]
        client.post(f"/v1/policy/change/{cid}/reject", json={})
        r2 = client.post(f"/v1/policy/change/{cid}/approve", json={})
        assert r2.status_code == 409


class TestStatusFilter:
    def test_filter_by_status(self, client: TestClient) -> None:
        client.post("/v1/policy/change", json={"title": "a"})
        r = client.post("/v1/policy/change", json={"title": "b"})
        cid = r.json()["change"]["id"]
        client.post(f"/v1/policy/change/{cid}/approve", json={})

        pending = client.get("/v1/policy/change", params={"status": "pending"})
        approved = client.get("/v1/policy/change", params={"status": "approved"})
        assert pending.json()["total"] == 1
        assert approved.json()["total"] == 1
