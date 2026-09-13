"""C3 策略配置中心的 HTTP 端点。

    GET  /v1/policy             当前生效策略总览（含文件指纹 / 工具策略 / watcher 状态）
    GET  /v1/policy/version     轻量版本号（供客户端秒级轮询做变更感知）
    GET  /v1/policy/files       策略文件指纹明细
    GET  /v1/policy/rules       L3 规则清单（支持 severity / tag / source / q 过滤）
    GET  /v1/policy/tools       L4 工具策略明细
    GET  /v1/policy/history     reload 历史（含失败与回滚记录）
    POST /v1/policy/reload      手动触发热更新（force 可强制 bump 版本）
    POST /v1/policy/validate    预检一段策略 YAML（不落盘，为 C4 铺路）

约定：所有响应都是"读当前快照"或"触发 reload 后读新快照"，无写入策略内容的接口
（写入属于 C4 自然语言转策略的职责）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.policy.registry import PolicyRegistry
from app.policy.watcher import PolicyWatcher

logger = logging.getLogger(__name__)


class ReloadRequest(BaseModel):
    """手动 reload 请求。"""

    force: bool = Field(default=False, description="内容未变时也强制 bump 版本号")


class ValidateRequest(BaseModel):
    """策略 YAML 预检请求。"""

    yaml: str = Field(..., description="待校验的策略 YAML 文本")


def create_policy_router(
    *,
    registry: PolicyRegistry | None = None,
    watcher: PolicyWatcher | None = None,
) -> APIRouter:
    """创建策略管理路由。"""
    reg = registry or PolicyRegistry()
    if registry is None:
        reg.reload(trigger="startup")

    router = APIRouter(tags=["policy"])

    # ---- 总览 ----

    @router.get("/v1/policy")
    async def get_policy(include_rules: bool = Query(default=True)) -> Any:
        """当前生效策略总览。"""
        snap = reg.snapshot
        body: dict[str, Any] = {
            "policy": snap.to_dict(include_rules=include_rules),
            "watcher": watcher.status() if watcher else {"running": False, "backend": "none"},
            "last_reload": reg.last_reload.to_dict() if reg.last_reload else None,
            "history_size": len(reg.history),
        }
        return body

    @router.get("/v1/policy/version")
    async def get_version() -> Any:
        """轻量版本号 —— 客户端可 1~2 秒轮询此端点感知策略变更。"""
        snap = reg.snapshot
        return {
            "revision": snap.revision,
            "version": snap.version,
            "loaded": snap.loaded,
            "loaded_at": snap.loaded_at,
            "loaded_at_iso": snap.loaded_at_iso,
            "rule_count": snap.rule_count,
            "file_count": snap.file_count,
            "warnings": len(snap.warnings),
        }

    # ---- 明细 ----

    @router.get("/v1/policy/files")
    async def get_files() -> Any:
        """策略文件指纹明细。"""
        snap = reg.snapshot
        return {
            "policy_dir": str(reg.policy_dir),
            "count": snap.file_count,
            "files": [f.to_dict() for f in snap.files],
            "warnings": list(snap.warnings),
        }

    @router.get("/v1/policy/rules")
    async def get_rules(
        severity: str | None = Query(default=None, description="low / medium / high"),
        tag: str | None = Query(default=None),
        source: str | None = Query(default=None, description="user / tool / system"),
        q: str | None = Query(default=None, description="在 id/description/message 里模糊匹配"),
    ) -> Any:
        """L3 规则清单（只读，供安全运营审阅）。"""
        rules = reg.rules
        if severity:
            rules = [r for r in rules if r.severity.value == severity]
        if tag:
            rules = [r for r in rules if tag in r.tags]
        if source:
            rules = [r for r in rules if any(s.value == source for s in r.sources)]
        if q:
            needle = q.lower()
            rules = [
                r
                for r in rules
                if needle in r.id.lower()
                or needle in (r.description or "").lower()
                or needle in (r.message or "").lower()
            ]
        return {
            "revision": reg.revision,
            "version": reg.version,
            "count": len(rules),
            "rules": [
                {
                    **r.to_dict(),
                    "effective_action": r.effective_action.value,
                }
                for r in rules
            ],
        }

    @router.get("/v1/policy/tools")
    async def get_tools() -> Any:
        """L4 工具策略明细。"""
        snap = reg.snapshot
        tp = snap.tool_policy
        return {
            "revision": snap.revision,
            "version": snap.version,
            "mode": "allowlist" if tp.allowlist else "blocklist",
            "blocked_tools": list(tp.blocked_tools),
            "allowlist": list(tp.allowlist),
            "dangerous_patterns": [
                {"name": n, "pattern": p, "message": m} for n, p, m in tp.dangerous_patterns
            ],
            "typed_tools": sorted(snap.tool_schemas),
        }

    @router.get("/v1/policy/history")
    async def get_history(limit: int = Query(default=20, ge=1, le=100)) -> Any:
        """reload 历史（最新在前），失败与回滚也记录在案。"""
        items = reg.history[:limit]
        return {
            "count": len(items),
            "history": [h.to_dict() for h in items],
        }

    # ---- 动作 ----

    @router.post("/v1/policy/reload")
    async def post_reload(body: ReloadRequest | None = None) -> Any:
        """手动触发热更新（YAML 放错时用 force 强行 bump 版本）。"""
        force = bool(body.force) if body else False
        result = reg.reload(trigger="api", force=force)
        status = 200 if result.ok else 422
        return JSONResponse(
            status_code=status,
            content={
                "result": result.to_dict(),
                "policy": reg.snapshot.to_dict(include_rules=False),
            },
        )

    @router.post("/v1/policy/validate")
    async def post_validate(body: ValidateRequest) -> Any:
        """预检一段策略 YAML（不落盘）。"""
        report = PolicyRegistry.validate_yaml(body.yaml)
        return JSONResponse(status_code=200 if report["ok"] else 422, content=report)

    return router


__all__ = ["ReloadRequest", "ValidateRequest", "create_policy_router"]
