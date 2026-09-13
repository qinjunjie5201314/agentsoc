"""D2 合规能力 · 策略变更审批台账。

面向安全/合规团队：每次策略变更都要留痕，可回答"谁、何时、为什么、改了什么、结果如何"。

设计（轻量但完整，适配单人/小团队）：
  - ``PolicyChange`` 表记录变更的**意图（proposal）**与**结果（applied/rejected）**
  - 流程：submit_proposal（提案，status=pending）→ approve（生效，触发 reload）→ 落账
  - 拒绝（reject）不生效，但仍留台账
  - 复用 ``PolicyRegistry.reload()`` 做实际生效，复用 ``revision`` 做版本关联

状态机：pending → approved / rejected
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChangeStatus(str, enum.Enum):
    """变更状态。"""

    PENDING = "pending"      # 已提案，待审批
    APPROVED = "approved"    # 已审批，已生效
    REJECTED = "rejected"    # 已拒绝，未生效


class PolicyChange(Base):
    """策略变更台账表。"""

    __tablename__ = "policy_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 提案内容
    title: Mapped[str] = mapped_column(String(256), nullable=False)  # 变更摘要
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    yaml_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)  # 变更的 YAML 片段
    proposed_by: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    # 状态与结果
    status: Mapped[ChangeStatus] = mapped_column(
        Enum(ChangeStatus, native_enum=False, length=16), nullable=False, index=True
    )
    # 生效后关联的策略版本
    policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 审批信息
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 时间
    proposed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True, default=_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "yaml_snippet": self.yaml_snippet,
            "proposed_by": self.proposed_by,
            "status": self.status.value,
            "policy_revision": self.policy_revision,
            "policy_version": self.policy_version,
            "reviewed_by": self.reviewed_by,
            "review_note": self.review_note,
            "proposed_at": self.proposed_at.isoformat() if self.proposed_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
        }


__all__ = ["ChangeStatus", "PolicyChange"]
