"""Fleet 终端注册表 —— 由桌面代理心跳上报驱动的进程级内存表。

设计要点：
  - 终端（desktop-proxy）每 N 秒把快照 POST 到 ``/v1/fleet/report``；
  - 本表以 ``agent_id``（默认主机名）为键，记录最近一次快照 + 上报时间；
  - 在线判定：``now - last_seen <= offline_after``（默认 180s = 3 x 60s 上报间隔）；
  - 纯内存 + 线程锁：进程重启即清空（后续可切 SQLite 做历史趋势）；
  - 对外只给视图（summary / list / get），不暴露内部状态对象。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_OFFLINE_AFTER = 180.0
DEFAULT_REPORT_INTERVAL = 60.0
MAX_EVENTS = 20
MAX_AGENTS = 2000
STALE_PURGE_SEC = 7 * 24 * 3600.0


@dataclass
class AgentState:
    """单台终端的最近状态。"""

    agent_id: str
    first_seen: float
    last_seen: float
    report_count: int = 0
    source_ip: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)


class FleetRegistry:
    """终端心跳注册表（线程安全）。"""

    def __init__(
        self,
        offline_after: float = DEFAULT_OFFLINE_AFTER,
        report_interval: float = DEFAULT_REPORT_INTERVAL,
        max_events: int = MAX_EVENTS,
        max_agents: int = MAX_AGENTS,
    ) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, AgentState] = {}
        self.offline_after = float(offline_after)
        self.report_interval = float(report_interval)
        self.max_events = int(max_events)
        self.max_agents = int(max_agents)

    # ---------- 写入 ----------

    def report(self, snapshot: dict[str, Any], source_ip: str = "") -> AgentState:
        """记录一次终端上报，返回更新后的内部状态。"""
        agent_id = self._agent_id(snapshot)
        now = time.time()
        with self._lock:
            st = self._agents.get(agent_id)
            if st is None:
                if len(self._agents) >= self.max_agents:
                    self._evict_locked()
                st = AgentState(agent_id=agent_id, first_seen=now, last_seen=now)
                self._agents[agent_id] = st
            st.last_seen = now
            st.report_count += 1
            if source_ip:
                st.source_ip = source_ip
            st.snapshot = self._trim(snapshot)
        return st

    # ---------- 只读视图 ----------

    def summary(self) -> dict[str, Any]:
        """总量 / 在线 / 离线 汇总（看板顶部大数字用）。"""
        now = time.time()
        with self._lock:
            total = len(self._agents)
            online = self.count_online_locked(now)
        return {
            "total": total,
            "online": online,
            "offline": total - online,
            "offline_after": self.offline_after,
            "report_interval": self.report_interval,
            "now": now,
        }

    def list_agents(self) -> list[dict[str, Any]]:
        """终端列表：在线优先，其次按最近上报时间倒序。"""
        now = time.time()
        with self._lock:
            views = [self._view(st, now) for st in self._agents.values()]
        views.sort(key=lambda v: (not v["online"], -float(v["last_seen"] or 0)))
        return views

    def get(self, agent_id: str) -> dict[str, Any] | None:
        """单台终端详情（含最近请求流水）。不存在返回 None。"""
        now = time.time()
        with self._lock:
            st = self._agents.get(agent_id)
            if st is None:
                return None
            view = self._view(st, now)
            recent = (st.snapshot or {}).get("recent") or []
            view["recent"] = list(recent)
        return view

    def count_online_locked(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return sum(1 for st in self._agents.values() if now - st.last_seen <= self.offline_after)

    # ---------- 维护 ----------

    def purge_stale(self, max_age: float = STALE_PURGE_SEC) -> int:
        """清理长期未上报的终端，避免内存无限增长。"""
        with self._lock:
            now = time.time()
            stale = [k for k, st in self._agents.items() if now - st.last_seen > max_age]
            for k in stale:
                self._agents.pop(k, None)
        return len(stale)

    def clear(self) -> None:
        with self._lock:
            self._agents.clear()

    # ---------- 内部 ----------

    def _evict_locked(self) -> int:
        """表满时踢掉最久未上报的 10%。"""
        items = sorted(self._agents.items(), key=lambda kv: kv[1].last_seen)
        drop = max(1, len(items) // 10)
        for key, _ in items[:drop]:
            self._agents.pop(key, None)
        return drop

    def _view(self, st: AgentState, now: float) -> dict[str, Any]:
        snap = st.snapshot or {}
        silent = max(0.0, now - st.last_seen)
        conn = snap.get("connectivity") or {}
        stats = snap.get("stats") or {}
        return {
            "agent_id": st.agent_id,
            "agent_name": snap.get("agent_name") or "",
            "hostname": snap.get("hostname") or st.agent_id,
            "online": silent <= self.offline_after,
            "last_seen": st.last_seen,
            "silent_sec": round(silent, 1),
            "first_seen": st.first_seen,
            "report_count": st.report_count,
            "source_ip": st.source_ip,
            "version": snap.get("version") or "",
            "mode": snap.get("mode") or "",
            "upstream": snap.get("upstream") or "",
            "start_time": snap.get("start_time") or 0,
            "uptime_sec": snap.get("uptime_sec") or 0,
            "connectivity": {
                "ok": bool(conn.get("ok")),
                "latency_ms": conn.get("latency_ms") or 0,
                "error": conn.get("error") or "",
            },
            "stats": {
                "total": stats.get("total") or 0,
                "blocked": stats.get("blocked") or 0,
                "passed": stats.get("passed") or 0,
                "errors": stats.get("errors") or 0,
            },
        }

    def _trim(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """只留最近 max_events 条流水，控制内存占用。"""
        snap = dict(snapshot)
        recent = snap.get("recent")
        if isinstance(recent, list) and len(recent) > self.max_events:
            snap["recent"] = recent[: self.max_events]
        return snap

    @staticmethod
    def _agent_id(snapshot: dict[str, Any]) -> str:
        """终端唯一标识：优先显式 agent_id，其次主机名。"""
        for key in ("agent_id", "hostname"):
            val = snapshot.get(key)
            if val:
                return str(val).strip()
        return "unknown"


__all__ = ["AgentState", "FleetRegistry", "DEFAULT_OFFLINE_AFTER", "DEFAULT_REPORT_INTERVAL"]
