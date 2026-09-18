"""Fleet 路由 —— 终端心跳上报 + 总览查询。

终端（desktop-proxy）侧写入：
    POST /v1/fleet/report        每 60s 上报一次运行快照

看板侧只读：
    GET  /v1/fleet/summary       总数 / 在线 / 离线
    GET  /v1/fleet/agents        终端列表（在线优先）
    GET  /v1/fleet/agents/{id}   单台终端详情（含最近请求流水）

鉴权说明：终端上报走中心统一的 ``X-API-Key``（设了 API_KEY 才强制）；
只读查询与看板页同为内网运维视图，沿用现有 demo 页策略不额外鉴权。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.fleet.registry import FleetRegistry
from app.security import require_api_key


def create_fleet_router(registry: FleetRegistry) -> APIRouter:
    """构造 fleet 路由；registry 为进程级单例，跨请求共享。"""
    router = APIRouter(tags=["fleet"])

    @router.post("/v1/fleet/report", dependencies=[Depends(require_api_key)])
    async def report(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """桌面代理心跳上报（终端每 60s 调用一次）。"""
        client = request.client.host if request.client else ""
        fwd = request.headers.get("x-forwarded-for", "")
        source_ip = (fwd.split(",")[0].strip() if fwd else "") or client
        st = registry.report(payload, source_ip=source_ip)
        return {
            "ok": True,
            "agent_id": st.agent_id,
            "report_count": st.report_count,
            "offline_after": registry.offline_after,
        }

    @router.get("/v1/fleet/summary")
    async def summary() -> dict[str, Any]:
        """终端在线 / 离线汇总。"""
        return registry.summary()

    @router.get("/v1/fleet/agents")
    async def list_agents() -> dict[str, Any]:
        """终端列表（含汇总，一次拉齐给看板用）。"""
        return {"summary": registry.summary(), "agents": registry.list_agents()}

    @router.get("/v1/fleet/agents/{agent_id}")
    async def agent_detail(agent_id: str) -> dict[str, Any]:
        """单台终端详情。"""
        detail = registry.get(agent_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"终端不存在: {agent_id}")
        return detail

    return router


__all__ = ["create_fleet_router"]
