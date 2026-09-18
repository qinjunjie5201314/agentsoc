"""Fleet 多终端总览 —— 终端心跳注册表 + 查询接口 + 看板页。

组成：
  - :class:`FleetRegistry`           进程级内存表（终端上报驱动，含在线判定）
  - :func:`create_fleet_router`      /v1/fleet/report（上报）+ /v1/fleet/agents（查询）
  - :func:`create_fleet_page_router` /fleet 总览页 + /fleet/agent/{id} 详情页
"""
from __future__ import annotations

from app.fleet.page import create_fleet_page_router
from app.fleet.registry import FleetRegistry
from app.fleet.routes import create_fleet_router

__all__ = [
    "FleetRegistry",
    "create_fleet_router",
    "create_fleet_page_router",
]
