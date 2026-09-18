"""Fleet 多终端总览 —— 注册表与上报接口测试。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.fleet.registry import FleetRegistry


def _snap(host: str = "PC-001", **kw) -> dict:
    snap = {
        "hostname": host,
        "agent_id": host,
        "version": "0.5.0",
        "mode": "explicit",
        "upstream": "http://center:8000",
        "uptime_sec": 100,
        "connectivity": {"ok": True, "latency_ms": 9, "error": ""},
        "stats": {"total": 4, "blocked": 1, "passed": 3, "errors": 0},
        "recent": [],
    }
    snap.update(kw)
    return snap


class TestFleetRegistry:
    """注册表：上报、在线判定、列表与详情。"""

    def test_empty_summary(self):
        reg = FleetRegistry()
        s = reg.summary()
        assert (s["total"], s["online"], s["offline"]) == (0, 0, 0)

    def test_report_registers_agent(self):
        reg = FleetRegistry()
        reg.report(_snap("PC-001"), source_ip="10.0.0.1")
        assert reg.summary()["total"] == 1
        assert reg.summary()["online"] == 1
        view = reg.list_agents()[0]
        assert view["agent_id"] == "PC-001"
        assert view["source_ip"] == "10.0.0.1"
        assert view["stats"]["blocked"] == 1
        assert view["online"] is True

    def test_agent_name_alias_kept_separate_from_id(self):
        reg = FleetRegistry()
        reg.report(_snap("PC-001", agent_name="财务部-小王"))
        view = reg.list_agents()[0]
        assert view["agent_name"] == "财务部-小王"
        assert view["agent_id"] == "PC-001"

    def test_falls_back_to_hostname_when_no_agent_id(self):
        reg = FleetRegistry()
        reg.report({"hostname": "ONLY-HOST", "agent_name": "别名不参与标识"})
        assert reg.list_agents()[0]["agent_id"] == "ONLY-HOST"

    def test_online_to_offline(self, monkeypatch):
        reg = FleetRegistry(offline_after=60)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        reg.report(_snap("PC-001"))
        assert reg.summary()["online"] == 1

        monkeypatch.setattr(time, "time", lambda: base + 59)
        assert reg.summary()["online"] == 1

        monkeypatch.setattr(time, "time", lambda: base + 61)
        assert reg.summary()["online"] == 0
        assert reg.summary()["offline"] == 1

    def test_list_online_first(self):
        reg = FleetRegistry(offline_after=10)
        reg.report(_snap("OLD-PC"))
        reg._agents["OLD-PC"].last_seen = time.time() - 100
        reg.report(_snap("NEW-PC"))
        assert [a["agent_id"] for a in reg.list_agents()][0] == "NEW-PC"

    def test_get_detail_and_missing(self):
        reg = FleetRegistry()
        reg.report(_snap("PC-001", recent=[{"ts": 1, "action": "block"}]))
        detail = reg.get("PC-001")
        assert detail is not None
        assert len(detail["recent"]) == 1
        assert reg.get("NOPE") is None

    def test_recent_trimmed_to_max_events(self):
        reg = FleetRegistry(max_events=3)
        reg.report(_snap("PC-001", recent=[{"ts": i} for i in range(10)]))
        assert len(reg.get("PC-001")["recent"]) == 3

    def test_report_count_increments(self):
        reg = FleetRegistry()
        for _ in range(3):
            reg.report(_snap("PC-001"))
        assert reg.list_agents()[0]["report_count"] == 3

    def test_purge_stale_removes_long_silent_agent(self):
        reg = FleetRegistry()
        reg.report(_snap("PC-001"))
        reg._agents["PC-001"].last_seen = time.time() - 10 * 24 * 3600
        assert reg.purge_stale() == 1
        assert reg.summary()["total"] == 0

    def test_capacity_evicts_oldest(self):
        reg = FleetRegistry(max_agents=3)
        for i in range(3):
            reg.report(_snap("PC-%d" % i))
            reg._agents["PC-%d" % i].last_seen = time.time() - (10 - i)
        reg.report(_snap("NEW"))
        assert reg.summary()["total"] <= 3


class TestFleetApi:
    """接口：上报、查询、页面。"""

    @pytest.fixture()
    def client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def test_report_then_list(self, client: TestClient):
        resp = client.post("/v1/fleet/report", json=_snap("API-PC"))
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "API-PC"

        data = client.get("/v1/fleet/agents").json()
        assert data["summary"]["total"] >= 1
        assert any(a["agent_id"] == "API-PC" for a in data["agents"])

    def test_summary_shape(self, client: TestClient):
        body = client.get("/v1/fleet/summary").json()
        for key in ("total", "online", "offline", "offline_after", "report_interval"):
            assert key in body

    def test_detail_unknown_agent_404(self, client: TestClient):
        assert client.get("/v1/fleet/agents/no-such-agent-xyz").status_code == 404

    def test_detail_after_report(self, client: TestClient):
        client.post("/v1/fleet/report", json=_snap("API-PC-2"))
        detail = client.get("/v1/fleet/agents/API-PC-2").json()
        assert detail["hostname"] == "API-PC-2"
        assert detail["online"] is True
        assert "recent" in detail

    def test_overview_page_renders(self, client: TestClient):
        resp = client.get("/fleet")
        assert resp.status_code == 200
        assert "终端总览" in resp.text
        assert "/v1/fleet/agents" in resp.text

    def test_detail_page_renders(self, client: TestClient):
        resp = client.get("/fleet/agent/API-PC")
        assert resp.status_code == 200
        assert "终端详情" in resp.text
