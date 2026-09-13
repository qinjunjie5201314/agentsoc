"""M1 三张核心表的 ORM 模型。

设计参考 M1-开发计划.md §5 数据模型：
- sessions：会话主表
- risk_events：风险事件（L1/L2/L3/L4 各层检测结果）
- audit_logs：审计日志（请求/响应/工具调用/工具结果全量）
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ============ 枚举 ============

class RiskLevel(str, enum.Enum):
    """风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskAction(str, enum.Enum):
    """处置动作。"""

    ALLOW = "allow"
    CONFIRM = "confirm"  # 二次确认
    BLOCK = "block"


class AuditEventType(str, enum.Enum):
    """审计事件类型。"""

    REQUEST = "request"
    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    POLICY_RELOAD = "policy_reload"
    ERROR = "error"


class SourceType(str, enum.Enum):
    """内容来源（L2 来源隔离用）。"""

    SYSTEM = "system"
    USER = "user"
    TOOL = "tool"
    UNKNOWN = "unknown"


# ============ sessions ============

class Session(Base):
    """会话主表。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)


# ============ risk_events ============

class RiskEvent(Base):
    """风险事件表。"""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    layer: Mapped[str] = mapped_column(String(8), nullable=False)  # L1/L2/L3/L4
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=16), nullable=False
    )
    action: Mapped[RiskAction] = mapped_column(
        Enum(RiskAction, native_enum=False, length=16), nullable=False
    )
    input_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


# ============ audit_logs ============

class AuditLog(Base):
    """审计日志表 - 每次请求/工具调用一条。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, native_enum=False, length=32), nullable=False, index=True
    )
    source: Mapped[SourceType | None] = mapped_column(
        Enum(SourceType, native_enum=False, length=16), nullable=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
