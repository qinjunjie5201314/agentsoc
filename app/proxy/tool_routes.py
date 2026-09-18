"""C2 工具调用 Hook 的 HTTP 端点。

三个端点：
  - ``POST /v1/tools/guard``   —— 只体检不执行（已有执行器的场景，把 AgentSoc 当"闸机"）
  - ``POST /v1/tools/execute`` —— 体检 + 执行（M1 dry-run），被拦的绝不执行
  - ``GET  /v1/tools/policy``  —— 查看当前生效的工具策略（黑名单/白名单/高危模式）

请求体统一为「协议 + payload」：
  ``{"protocol": "openai", "payload": {"tool_calls": [...]}}``
  也支持直接传 ``tool_calls`` 数组，或 Anthropic 的 ``content`` 块列表。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.audit.models import RiskAction
from app.proxy.tool_executor import DryRunToolExecutor
from app.proxy.tool_hook import (
    ToolGuard,
    build_tool_result_error,
    build_tool_result_ok,
    extract_tool_calls,
)

logger = logging.getLogger(__name__)


class ToolCallRequest(BaseModel):
    """工具调用守卫/执行请求。"""

    protocol: str = "auto"  # auto | openai | anthropic
    payload: Any = None  # 任意形状（assistant message / 响应体 / content 块列表）
    tool_calls: list[dict[str, Any]] | None = None  # 快捷写法
    dry_run: bool = True  # execute 端点专用；M1 恒为 dry-run
    include_tool_results: bool = True  # 是否返回 tool_result 消息


def create_tool_router(
    *,
    guard: ToolGuard | None = None,
    executor: DryRunToolExecutor | None = None,
) -> APIRouter:
    """创建 C2 工具调用路由。"""
    g = guard or ToolGuard()
    ex = executor or DryRunToolExecutor()
    router = APIRouter(tags=["tools"])

    def _resolve_calls(body: ToolCallRequest) -> list:
        if body.tool_calls is not None:
            return extract_tool_calls({"tool_calls": body.tool_calls}, protocol=body.protocol)
        if body.payload is None:
            return []
        return extract_tool_calls(body.payload, protocol=body.protocol)

    @router.post("/v1/tools/guard")
    async def guard_tools(body: ToolCallRequest) -> Any:
        """只做 L4 体检，不执行任何工具。"""
        calls = _resolve_calls(body)
        result = g.guard_calls(calls)
        content: dict[str, Any] = {"agentsentry": {"tools": result.to_dict()}}

        if body.include_tool_results:
            content["tool_results"] = [
                build_tool_result_error(guarded) for guarded in result.blocked
            ]

        # 严格模式：只要有工具被拦就返回 403，方便调用方 fail-fast
        status = 403 if result.action is RiskAction.BLOCK else 200
        return JSONResponse(status_code=status, content=content)

    @router.post("/v1/tools/execute")
    async def execute_tools(body: ToolCallRequest) -> Any:
        """体检 + 执行。被拦的工具绝不进入执行器。"""
        calls = _resolve_calls(body)
        result = g.guard_calls(calls)

        executions: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for guarded in result.guarded:
            if guarded.blocked:
                executions.append(
                    {
                        "name": guarded.call.name,
                        "call_id": guarded.call.call_id,
                        "status": "blocked",
                        "reason": guarded.decision.reason,
                        "executed": False,
                    }
                )
                tool_results.append(build_tool_result_error(guarded))
            else:
                er = ex.execute(guarded.call.name, guarded.call.arguments)
                executions.append(
                    {
                        "name": guarded.call.name,
                        "call_id": guarded.call.call_id,
                        "status": "executed" if er.ok else "failed",
                        "executed": True,
                        "dry_run": er.dry_run,
                        "output": er.output,
                        "error": er.error,
                        "elapsed_ms": round(er.elapsed_ms, 3),
                    }
                )
                tool_results.append(
                    build_tool_result_ok(guarded, DryRunToolExecutor.as_text(er))
                )

        content: dict[str, Any] = {
            "agentsentry": {"tools": result.to_dict()},
            "executions": executions,
        }
        if body.include_tool_results:
            content["tool_results"] = tool_results

        status = 403 if result.action is RiskAction.BLOCK else 200
        return JSONResponse(status_code=status, content=content)

    @router.get("/v1/tools/policy")
    async def get_policy() -> Any:
        """查看当前生效的工具策略摘要。"""
        return {
            "mode": "allowlist" if g.policy.allowlist else "blocklist",
            "blocked_tools": g.policy.blocked_tools,
            "allowlist": g.policy.allowlist,
            "dangerous_patterns": [
                {"name": n, "pattern": p, "message": m}
                for n, p, m in g.policy.dangerous_patterns
            ],
            "typed_tools": sorted(g.schemas.keys()),
        }

    return router


__all__ = ["ToolCallRequest", "create_tool_router"]
