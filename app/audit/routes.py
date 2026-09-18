"""D1 审计查询端点 + D2 合规报表。

端点:
    GET /v1/audit/status           落库统计（队列/已写/丢弃）
    GET /v1/audit/events           风险事件列表（按 session/layer/severity/action 过滤 + 分页）
    GET /v1/audit/sessions         会话列表
    GET /v1/audit/sessions/{id}    单会话全链路回放（含 risk_events + audit_logs）
    GET /v1/audit/sessions/{id}/timeline  按时间排序的完整 timeline
    GET /v1/audit/report           D2 审计报表（汇总 + TOP 规则 + 分布 + 趋势）
    GET /v1/audit/report/export    D2 报表导出（json / csv）
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.audit.logger import AuditLogger
from app.audit.models import AuditLog, RiskEvent, Session
from app.audit.replay import build_timeline
from app.audit.reports import build_report, export_csv, export_json
from app.db import get_db


def create_audit_router(
    *,
    audit: AuditLogger | None = None,
    db_dep: Callable | None = None,
) -> APIRouter:
    """创建审计查询路由。

    Args:
        audit: 审计落库器（用于 /v1/audit/status）。
        db_dep: 查询用的 Session 依赖（默认 ``app.db.get_db``；测试注入临时库）。
    """
    router = APIRouter(tags=["audit"])
    _audit = audit
    _db_dep = db_dep or get_db

    # ---- 状态 ----

    @router.get("/v1/audit/status")
    async def audit_status() -> dict[str, Any]:
        if _audit is None:
            return {"enabled": False, "running": False, "reason": "audit logger 未注入"}
        return _audit.stats

    # ---- 风险事件 ----

    @router.get("/v1/audit/events")
    async def list_events(
        session_id: str | None = Query(default=None),
        layer: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        action: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc())
        if session_id:
            stmt = stmt.where(RiskEvent.session_id == session_id)
        if layer:
            stmt = stmt.where(RiskEvent.layer == layer)
        if severity:
            stmt = stmt.where(RiskEvent.risk_level == severity)
        if action:
            stmt = stmt.where(RiskEvent.action == action)
        total = len(db.scalars(stmt).all())
        rows = db.scalars(stmt.limit(limit).offset(offset)).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "events": [_event_to_dict(e) for e in rows],
        }

    # ---- 会话 ----

    @router.get("/v1/audit/sessions")
    async def list_sessions(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        stmt = select(Session).order_by(Session.started_at.desc())
        total = len(db.scalars(stmt).all())
        rows = db.scalars(stmt.limit(limit).offset(offset)).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "sessions": [
                {
                    "id": s.id,
                    "user_id": s.user_id,
                    "agent_id": s.agent_id,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "meta": s.meta,
                }
                for s in rows
            ],
        }

    @router.get("/v1/audit/sessions/{session_id}")
    async def get_session_replay(
        session_id: str,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        session = db.get(Session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

        risk_stmt = (
            select(RiskEvent)
            .where(RiskEvent.session_id == session_id)
            .order_by(RiskEvent.created_at.asc())
        )
        risk_events = db.scalars(risk_stmt).all()

        log_stmt = (
            select(AuditLog)
            .where(AuditLog.session_id == session_id)
            .order_by(AuditLog.created_at.asc())
        )
        logs = db.scalars(log_stmt).all()

        return {
            "session": {
                "id": session.id,
                "user_id": session.user_id,
                "agent_id": session.agent_id,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            },
            "risk_events": [_event_to_dict(e) for e in risk_events],
            "audit_logs": [
                {
                    "event_type": l.event_type.value,
                    "source": l.source.value if l.source else None,
                    "payload": l.payload,
                    "normalized": l.normalized,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                }
                for l in logs
            ],
        }

    @router.get("/v1/audit/sessions/{session_id}/timeline")
    async def get_session_timeline(
        session_id: str,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """按时间排序的完整 timeline（D3 会话重放）。"""
        # build_timeline 需要 session_factory，这里包一层用当前依赖注入的 db
        def _factory():
            return db

        result = build_timeline(session_id, session_factory=_factory, close_session=False)
        if result["session"] is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        return result

    # ---- D2 合规能力：审计报表 ----

    @router.get("/v1/audit/report")
    async def get_report(
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        top_n: int = Query(default=10, ge=1, le=100),
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """审计报表（汇总 + 规则 TOP + 来源/分层分布 + 日趋势）。

        start/end 支持 ISO 时间或相对时间（如 "24h"/"7d"/"30d"）。
        """
        return build_report(db, start=start, end=end, top_n=top_n)

    @router.get("/v1/audit/report/export")
    async def export_report(
        fmt: str = Query(default="json", pattern=r"^(json|csv)$"),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        db: OrmSession = Depends(_db_dep),
    ):
        """导出报表（json 或 csv）。"""
        from fastapi.responses import PlainTextResponse, Response

        report = build_report(db, start=start, end=end)
        if fmt == "csv":
            content = export_csv(report)
            return Response(
                content=content,
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=agentsoc-report.csv"},
            )
        content = export_json(report)
        return PlainTextResponse(
            content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=agentsoc-report.json"},
        )

    return router


def _event_to_dict(e: RiskEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "session_id": e.session_id,
        "layer": e.layer,
        "rule_id": e.rule_id,
        "risk_level": e.risk_level.value if e.risk_level else None,
        "action": e.action.value if e.action else None,
        "input_snippet": e.input_snippet,
        "matched": e.matched,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


__all__ = ["create_audit_router"]
