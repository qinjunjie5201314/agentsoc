"""D1 审计落库器 - 单元测试。

用注入 ``session_factory`` 的方式隔离临时数据库（不碰 settings.database_url）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from app.audit.logger import AuditLogger
from app.audit.models import AuditLog, RiskEvent, Session
from app.db import Base


@pytest.fixture()
def engine(tmp_path: Path):
    """全新临时 SQLite engine + 建表。"""
    eng = sa.create_engine(
        f"sqlite:///{tmp_path / 'audit_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def factory(engine):
    """返回绑定临时 engine 的 Session 工厂。"""
    def _factory():
        return sa.orm.Session(engine, expire_on_commit=False)
    return _factory


@pytest.fixture()
def audit(factory):
    """已启动的 AuditLogger（用临时库）。"""
    a = AuditLogger(session_factory=factory, flush_interval=0.01)
    a.start()
    yield a
    a.shutdown()


class TestAuditLoggerBasics:
    def test_log_detection_writes_three_tables(self, audit, engine) -> None:
        # 构造一个"假"检测结果对象（只要有所需属性即可）
        class FakeHit:
            layer = "L3"
            rule_id = "rule_ignore"
            severity = "high"
            matched_text = "Ignore all previous"
            source = "user"
            tags = ["prompt_injection"]

        class FakeResult:
            hits = [FakeHit()]
            risk_level = "high"
            final_action = "block"
            policy_version = "abc123"
            policy_revision = 7

        audit.log_detection(
            session_id="s1",
            result=FakeResult(),
            messages=[{"role": "user", "content": "x"}],
        )
        audit.shutdown()

        with sa.orm.Session(engine) as db:
            assert db.query(Session).count() == 1
            assert db.query(RiskEvent).count() == 1
            assert db.query(AuditLog).count() >= 2  # request + risk_event 的 request

    def test_log_detection_no_hits_still_writes_one_risk_event(self, audit, engine) -> None:
        class FakeResult:
            hits = []
            risk_level = "low"
            final_action = "allow"
            policy_version = None
            policy_revision = None

        audit.log_detection(session_id="s2", result=FakeResult())
        audit.shutdown()

        with sa.orm.Session(engine) as db:
            assert db.query(RiskEvent).count() == 1
            e = db.query(RiskEvent).first()
            assert e.layer == "L1+L2+L3"
            assert e.action.value == "allow"

    def test_log_tool_call_blocked_writes_risk_event(self, audit, engine) -> None:
        audit.log_tool_call(
            session_id="s3",
            tool_name="bash",
            arguments={"cmd": "rm -rf /"},
            status="blocked",
            reason="blacklisted",
        )
        audit.shutdown()

        with sa.orm.Session(engine) as db:
            # blocked 工具 → 2 audit_log（tool_call 主日志 + risk_event 占位）+ 1 risk_event(L4)
            assert db.query(AuditLog).count() == 2
            assert db.query(RiskEvent).count() == 1
            e = db.query(RiskEvent).first()
            assert e.layer == "L4"
            assert e.action.value == "block"
            assert e.rule_id == "tool:bash"

    def test_log_tool_call_executed_no_risk_event(self, audit, engine) -> None:
        audit.log_tool_call(
            session_id="s4",
            tool_name="get_weather",
            arguments={"city": "Beijing"},
            status="executed",
        )
        audit.shutdown()

        with sa.orm.Session(engine) as db:
            assert db.query(AuditLog).count() == 1
            assert db.query(RiskEvent).count() == 0  # 正常执行不写 risk_event


class TestAuditLoggerFailSafe:
    def test_disabled_logger_noops(self, factory) -> None:
        a = AuditLogger(session_factory=factory, enabled=False)
        a.log(session_id="s", event_type="request")
        assert a.stats["written"] == 0

    def test_log_does_not_raise_on_bad_session(self, factory) -> None:
        """即使 session_factory 抛异常，log 也不该让调用方崩溃。"""
        def bad_factory():
            raise RuntimeError("db down")
        a = AuditLogger(session_factory=bad_factory, enabled=True)
        a.start()
        # log_detection 里 _ensure_session 会捕获异常
        class FakeHit:
            layer = "L3"; rule_id = "r"; severity = "high"; matched_text = "x"; source = "user"; tags = []
        class FakeResult:
            hits = [FakeHit()]; risk_level = "high"; final_action = "block"; policy_version = None; policy_revision = None
        # 不应抛出
        a.log_detection(session_id="s", result=FakeResult())
        a.shutdown()
        # 落库失败被吞掉，但进程不崩
        assert a.stats["written"] >= 0


class TestAuditLoggerStats:
    def test_stats_shape(self, audit) -> None:
        s = audit.stats
        assert "enabled" in s
        assert "running" in s
        assert "queue_size" in s
        assert "written" in s
        assert "dropped" in s
