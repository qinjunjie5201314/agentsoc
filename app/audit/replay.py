"""D3 会话重放 —— 根据 session_id 生成完整 timeline。

把一次会话的审计数据组织成按时间排序的「事件链」，用于：
  - 安全团队复盘一次攻击（原始输入 → L1 归一化 → L3 命中 → L4 拦截）
  - 合规审计（每一步都有时间戳 + 策略版本可溯源）

纯函数 + 依赖注入（``session_factory``），便于测试和复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.audit.models import AuditLog, RiskEvent, Session


@dataclass
class TimelineEvent:
    """timeline 中的单个事件（时间排序）。"""

    at: str
    kind: str              # session_start / request / risk_hit / tool_call / ...
    layer: str | None = None
    rule_id: str | None = None
    risk_level: str | None = None
    action: str | None = None
    source: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _iso(ts: Any) -> str:
    if ts is None:
        return ""
    return ts.isoformat(timespec="seconds")


def build_timeline(
    session_id: str,
    *,
    session_factory: Callable[[], Any],
    close_session: bool = True,
) -> dict[str, Any]:
    """生成一次会话的完整 timeline。

    Args:
        session_factory: 返回 Session 的工厂。
        close_session: 是否在结束时 close session（FastAPI 依赖注入时传 False，
            由框架管理 session 生命周期）。

    返回结构：
        {
          "session": {...},
          "events": [ {at, kind, layer, rule_id, risk_level, action, detail}, ... ],
          "summary": {"risk_events": N, "audit_logs": M, "blocked": bool}
        }
    """
    db = session_factory()
    try:
        session = db.get(Session, session_id)
        if session is None:
            return {"session": None, "events": [], "summary": {}}

        # 1) session 起点
        events: list[TimelineEvent] = [
            TimelineEvent(
                at=_iso(session.started_at),
                kind="session_start",
                detail={"user_id": session.user_id, "agent_id": session.agent_id},
            )
        ]

        # 2) 风险事件（按时间）
        risks = (
            db.query(RiskEvent)
            .filter(RiskEvent.session_id == session_id)
            .order_by(RiskEvent.created_at.asc())
            .all()
        )
        for r in risks:
            events.append(TimelineEvent(
                at=_iso(r.created_at),
                kind="risk_hit",
                layer=r.layer,
                rule_id=r.rule_id,
                risk_level=r.risk_level.value if r.risk_level else None,
                action=r.action.value if r.action else None,
                detail={
                    "snippet": r.input_snippet,
                    "matched": r.matched,
                },
            ))

        # 3) 审计日志（request / tool_call / policy_reload / error）
        logs = (
            db.query(AuditLog)
            .filter(AuditLog.session_id == session_id)
            .order_by(AuditLog.created_at.asc())
            .all()
        )
        for l in logs:
            kind = l.event_type.value if l.event_type else "unknown"
            # request 日志里可能带了 l1_preview / normalized
            detail: dict[str, Any] = {}
            if l.payload:
                detail = dict(l.payload)
            if l.normalized:
                detail.setdefault("l1_preview", l.normalized)
            events.append(TimelineEvent(
                at=_iso(l.created_at),
                kind=kind,
                source=(l.source.value if l.source else None),
                detail=detail,
            ))

        # 4) session 结束（若有）
        if session.ended_at:
            events.append(TimelineEvent(
                at=_iso(session.ended_at),
                kind="session_end",
            ))

        # 按时间排序（kind 稳定排序：session_start 最先）
        events.sort(key=lambda e: e.at)

        blocked = any(e.action == "block" for e in events)
        return {
            "session": {
                "id": session.id,
                "user_id": session.user_id,
                "agent_id": session.agent_id,
                "started_at": _iso(session.started_at),
                "ended_at": _iso(session.ended_at),
            },
            "events": [
                {
                    "at": e.at,
                    "kind": e.kind,
                    "layer": e.layer,
                    "rule_id": e.rule_id,
                    "risk_level": e.risk_level,
                    "action": e.action,
                    "source": e.source,
                    "detail": e.detail,
                }
                for e in events
            ],
            "summary": {
                "risk_events": len(risks),
                "audit_logs": len(logs),
                "blocked": blocked,
            },
        }
    finally:
        if close_session:
            db.close()


__all__ = ["TimelineEvent", "build_timeline"]
