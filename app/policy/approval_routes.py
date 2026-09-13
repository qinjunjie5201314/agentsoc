"""D2 合规能力 · 策略变更审批端点。

端点:
    POST /v1/policy/change           提交变更提案（pending）
    GET  /v1/policy/change           变更台账列表（过滤 + 分页）
    GET  /v1/policy/change/{id}      单条变更详情
    POST /v1/policy/change/{id}/approve   审批通过（触发生效）
    POST /v1/policy/change/{id}/reject    拒绝
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.db import get_db
from app.policy.change_log import ChangeStatus, PolicyChange
from app.policy.registry import PolicyRegistry


class ChangeProposalRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    yaml_snippet: str | None = None
    proposed_by: str = "unknown"


class ChangeReviewRequest(BaseModel):
    reviewed_by: str = "admin"
    review_note: str | None = None


def create_approval_router(
    *,
    registry: PolicyRegistry,
    db_dep: Callable | None = None,
) -> APIRouter:
    """策略变更审批路由。"""
    router = APIRouter(tags=["policy", "compliance"])
    _db_dep = db_dep or get_db

    @router.post("/v1/policy/change")
    async def submit_proposal(
        req: ChangeProposalRequest,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """提交变更提案（pending）。"""
        change = PolicyChange(
            title=req.title,
            description=req.description,
            yaml_snippet=req.yaml_snippet,
            proposed_by=req.proposed_by,
            status=ChangeStatus.PENDING,
        )
        db.add(change)
        db.commit()
        db.refresh(change)
        return {"ok": True, "change": change.to_dict()}

    @router.get("/v1/policy/change")
    async def list_changes(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """变更台账列表。"""
        stmt = select(PolicyChange).order_by(PolicyChange.proposed_at.desc())
        if status:
            stmt = stmt.where(PolicyChange.status == status)
        total = len(db.scalars(stmt).all())
        rows = db.scalars(stmt.limit(limit).offset(offset)).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "changes": [c.to_dict() for c in rows],
        }

    @router.get("/v1/policy/change/{change_id}")
    async def get_change(
        change_id: int,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """单条变更详情。"""
        change = db.get(PolicyChange, change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"变更 {change_id} 不存在")
        return change.to_dict()

    @router.post("/v1/policy/change/{change_id}/approve")
    async def approve_change(
        change_id: int,
        req: ChangeReviewRequest,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """审批通过：标记 approved，并触发策略生效（如有 YAML 片段则写盘 + reload）。"""
        change = db.get(PolicyChange, change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"变更 {change_id} 不存在")
        if change.status is not ChangeStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"变更已是 {change.status.value} 状态")

        # 若提案带 YAML 片段，写盘并 reload
        reload_result = None
        if change.yaml_snippet:
            target = registry.policy_dir / f"change_{change_id}.yaml"
            target.write_text(change.yaml_snippet, encoding="utf-8")
            reload_result = registry.reload(trigger=f"change_approve_{change_id}", force=True)

        change.status = ChangeStatus.APPROVED
        change.reviewed_by = req.reviewed_by
        change.review_note = req.review_note
        change.reviewed_at = datetime.now(timezone.utc)
        if reload_result is not None:
            change.policy_revision = reload_result.revision
            change.policy_version = reload_result.version
        db.commit()
        db.refresh(change)

        return {
            "ok": True,
            "change": change.to_dict(),
            "reload": (
                {
                    "revision": reload_result.revision,
                    "version": reload_result.version,
                    "ok": reload_result.ok,
                    "errors": list(reload_result.errors),
                }
                if reload_result is not None
                else None
            ),
        }

    @router.post("/v1/policy/change/{change_id}/reject")
    async def reject_change(
        change_id: int,
        req: ChangeReviewRequest,
        db: OrmSession = Depends(_db_dep),
    ) -> dict[str, Any]:
        """拒绝变更：标记 rejected，不生效。"""
        change = db.get(PolicyChange, change_id)
        if change is None:
            raise HTTPException(status_code=404, detail=f"变更 {change_id} 不存在")
        if change.status is not ChangeStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"变更已是 {change.status.value} 状态")

        change.status = ChangeStatus.REJECTED
        change.reviewed_by = req.reviewed_by
        change.review_note = req.review_note
        change.reviewed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(change)
        return {"ok": True, "change": change.to_dict()}

    return router


__all__ = [
    "ChangeProposalRequest",
    "ChangeReviewRequest",
    "create_approval_router",
]
