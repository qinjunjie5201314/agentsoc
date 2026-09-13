"""D2 合规能力 · 审计报表 - 单元测试。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import (
    AuditEventType,
    AuditLog,
    RiskAction,
    RiskEvent,
    RiskLevel,
    Session,
    SourceType,
)
from app.audit.reports import build_report, export_csv, export_json, _parse_time
from app.db import Base


@pytest.fixture()
def engine(tmp_path: Path):
    eng = sa.create_engine(
        f"sqlite:///{tmp_path / 'report_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


def _seed(engine) -> None:
    """插入测试数据：3 条 risk_event + 2 会话 + 若干 audit_log。"""
    now = datetime.now(UTC)
    with OrmSession(engine) as db:
        db.add(Session(id="s1", user_id="u1", agent_id="a1", started_at=now))
        db.add(Session(id="s2", user_id="u2", agent_id="a1", started_at=now - timedelta(days=1)))

        # 3 条风险事件：2 block + 1 allow
        db.add(RiskEvent(
            session_id="s1", layer="L3", rule_id="rule_a",
            risk_level=RiskLevel.HIGH, action=RiskAction.BLOCK,
            input_snippet="ignore all", created_at=now,
        ))
        db.add(RiskEvent(
            session_id="s1", layer="L3", rule_id="rule_a",
            risk_level=RiskLevel.HIGH, action=RiskAction.BLOCK,
            input_snippet="ignore all 2", created_at=now,
        ))
        db.add(RiskEvent(
            session_id="s2", layer="L4", rule_id="tool:bash",
            risk_level=RiskLevel.MEDIUM, action=RiskAction.ALLOW,
            input_snippet="rm -rf", created_at=now - timedelta(days=1),
        ))

        # audit_log：不同 source
        db.add(AuditLog(
            session_id="s1", event_type=AuditEventType.REQUEST,
            source=SourceType.USER, created_at=now,
        ))
        db.add(AuditLog(
            session_id="s1", event_type=AuditEventType.TOOL_CALL,
            source=SourceType.TOOL, created_at=now,
        ))
        db.commit()


class TestParseTime:
    def test_relative_hours(self):
        now = datetime.now(UTC)
        assert _parse_time("24h", default=now, now=now) == now - timedelta(hours=24)

    def test_relative_days(self):
        now = datetime.now(UTC)
        assert _parse_time("7d", default=now, now=now) == now - timedelta(days=7)

    def test_empty_uses_default(self):
        now = datetime.now(UTC)
        assert _parse_time(None, default=now, now=now) == now

    def test_iso_format(self):
        now = datetime.now(UTC)
        iso = "2026-09-01T00:00:00+00:00"
        assert _parse_time(iso, default=now, now=now).year == 2026


class TestBuildReport:
    def test_summary_counts(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db, start="30d")
        assert r["summary"]["risk_events"] == 3
        assert r["summary"]["blocked"] == 2
        assert r["summary"]["allowed"] == 1
        assert r["summary"]["sessions"] == 2
        # 误报率 = allow / total = 1/3
        assert abs(r["summary"]["false_positive_rate"] - 0.3333) < 0.01

    def test_top_rules(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db, start="30d")
        # rule_a 出现 2 次，排第一
        assert r["top_rules"][0]["rule_id"] == "rule_a"
        assert r["top_rules"][0]["count"] == 2

    def test_source_distribution(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db, start="30d")
        sources = {d["source"]: d["count"] for d in r["source_distribution"]}
        assert sources.get("user") == 1
        assert sources.get("tool") == 1

    def test_layer_distribution(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db, start="30d")
        layers = {d["layer"]: d["count"] for d in r["layer_distribution"]}
        assert layers.get("L3") == 2
        assert layers.get("L4") == 1

    def test_time_filter(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            # 只查最近 1 小时 → 只有 now 时刻的 2 条（s2 是昨天）
            r = build_report(db, start="1h")
        assert r["summary"]["risk_events"] == 2

    def test_period_present(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db)
        assert "start" in r["period"]
        assert "end" in r["period"]


class TestExport:
    def test_export_json(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db)
        s = export_json(r)
        assert '"risk_events"' in s
        assert '"top_rules"' in s

    def test_export_csv(self, engine):
        _seed(engine)
        with OrmSession(engine) as db:
            r = build_report(db)
        s = export_csv(r)
        assert "AgentSoc 审计报表" in s
        assert "命中规则 TOP" in s
        assert "rule_a" in s
