"""审计模块 - D1 审计日志落库与查询。

对外暴露：
  - ``AuditLogger``   —— 异步落库器（sessions / risk_events / audit_logs 三表）
  - ``create_audit_router`` —— /v1/audit/* 查询端点
  - ``Session`` / ``RiskEvent`` / ``AuditLog`` —— ORM 模型
"""

from app.audit.logger import AuditLogger, AuditRecord
from app.audit.models import (
    AuditEventType,
    AuditLog,
    RiskAction,
    RiskEvent,
    RiskLevel,
    Session,
    SourceType,
)
from app.audit.replay import TimelineEvent, build_timeline
from app.audit.reports import build_report, export_csv, export_json
from app.audit.routes import create_audit_router

__all__ = [
    "AuditEventType",
    "AuditLog",
    "AuditLogger",
    "AuditRecord",
    "RiskAction",
    "RiskEvent",
    "RiskLevel",
    "Session",
    "SourceType",
    "TimelineEvent",
    "build_report",
    "build_timeline",
    "create_audit_router",
    "export_csv",
    "export_json",
]
