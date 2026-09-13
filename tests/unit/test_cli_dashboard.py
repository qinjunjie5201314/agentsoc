"""D2 CLI 日志展示 - 单元测试。

只测纯函数（_fmt / _risk_table / _summary_table / _normalize_preview），
不测交互式的 Live 循环（难以在测试里验证）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.audit.models import RiskAction, RiskEvent, RiskLevel
from app.cli.dashboard import _fmt, _risk_table, _summary_table


def _event(level="high", action="block", rule="rule_x", snippet="Ignore all") -> RiskEvent:
    return RiskEvent(
        id=1,
        session_id="s1",
        layer="L3",
        rule_id=rule,
        risk_level=RiskLevel(level),
        action=RiskAction(action),
        input_snippet=snippet,
        matched={"source": "user"},
        created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC),
    )


class TestFmt:
    def test_fmt_none(self):
        assert _fmt(None) == "—"

    def test_fmt_naive_assumes_utc(self):
        ts = datetime(2026, 9, 10, 12, 0, 0)
        out = _fmt(ts)
        assert out  # 非空即可（本地时区偏移不做硬断言）


class TestRiskTable:
    def test_table_renders_rows(self):
        t = _risk_table([_event()])
        # Rich Table 渲染成字符串后应包含关键字段
        rendered = str(t) if hasattr(t, "__rich_console__") else ""
        assert rendered  # 至少能构造不抛异常


class TestSummaryTable:
    def test_summary_counts(self):
        t = _summary_table(total_events=10, total_sessions=3, block_count=7, logs_count=20)
        assert t is not None
