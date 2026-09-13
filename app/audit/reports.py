"""D2 合规能力 · 审计报表与导出。

面向安全/合规团队的周期报表：
  - 汇总统计：拦截量、风险事件数、会话数、工具调用数
  - 命中规则 TOP：哪条规则拦得最多
  - 来源分布：system / user / tool 来源的事件分布
  - 时间趋势：按小时/天聚合

导出：CSV（零依赖，标准库 csv），可选 JSON。

设计原则：
  - 纯查询 + 聚合，不写库
  - 支持时间范围过滤（start / end，ISO 格式或相对时间）
  - 报表结果可直接用于对外审计汇报
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.models import AuditLog, RiskEvent, Session


def _parse_time(value: str | None, *, default: datetime, now: datetime | None = None) -> datetime:
    """解析时间参数。支持 ISO 格式；空则用默认值。

    Args:
        value: 时间字符串（ISO 或相对时间如 "24h"/"7d"）。
        default: 空值时的默认（通常是"最近 N 天前"）。
        now: 相对时间的基准（默认取系统当前 UTC）。
    """
    if not value:
        return default
    base = now or datetime.now(timezone.utc)
    # 相对时间："24h" / "7d" / "30d"（相对 now）
    if value.endswith("h") and value[:-1].isdigit():
        return base - timedelta(hours=int(value[:-1]))
    if value.endswith("d") and value[:-1].isdigit():
        return base - timedelta(days=int(value[:-1]))
    # ISO 格式
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return default


def build_report(
    db: OrmSession,
    *,
    start: str | None = None,
    end: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """生成审计报表。

    Args:
        db: SQLAlchemy Session。
        start: 起始时间（ISO 或相对时间如 "24h"/"7d"）。默认最近 7 天。
        end: 结束时间。默认当前。
        top_n: 命中规则 TOP N。

    Returns:
        报表 dict，含 summary / top_rules / source_distribution / daily_trend。
    """
    now = datetime.now(timezone.utc)
    start_dt = _parse_time(start, default=now - timedelta(days=7), now=now)
    end_dt = _parse_time(end, default=now, now=now)

    # ---- 1) 汇总 ----
    risk_total = db.scalar(
        select(func.count(RiskEvent.id)).where(
            RiskEvent.created_at >= start_dt, RiskEvent.created_at <= end_dt
        )
    ) or 0
    blocked = db.scalar(
        select(func.count(RiskEvent.id)).where(
            RiskEvent.created_at >= start_dt,
            RiskEvent.created_at <= end_dt,
            RiskEvent.action == "block",
        )
    ) or 0
    sessions_total = db.scalar(
        select(func.count(Session.id)).where(
            Session.started_at >= start_dt, Session.started_at <= end_dt
        )
    ) or 0
    logs_total = db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= start_dt, AuditLog.created_at <= end_dt
        )
    ) or 0

    # ---- 2) 命中规则 TOP ----
    top_rows = db.execute(
        select(RiskEvent.rule_id, func.count(RiskEvent.id).label("cnt"))
        .where(RiskEvent.created_at >= start_dt, RiskEvent.created_at <= end_dt)
        .group_by(RiskEvent.rule_id)
        .order_by(func.count(RiskEvent.id).desc())
        .limit(top_n)
    ).all()
    top_rules = [
        {"rule_id": r[0] or "unknown", "count": r[1]} for r in top_rows
    ]

    # ---- 3) 来源分布（risk_events 没有 source 字段，从 audit_logs 统计）----
    src_rows = db.execute(
        select(AuditLog.source, func.count(AuditLog.id).label("cnt"))
        .where(AuditLog.created_at >= start_dt, AuditLog.created_at <= end_dt)
        .group_by(AuditLog.source)
        .order_by(func.count(AuditLog.id).desc())
    ).all()
    source_dist = [
        {"source": (r[0].value if r[0] else "unknown"), "count": r[1]}
        for r in src_rows
    ]

    # ---- 4) 分层分布（L1/L2/L3/L4）----
    layer_rows = db.execute(
        select(RiskEvent.layer, func.count(RiskEvent.id).label("cnt"))
        .where(RiskEvent.created_at >= start_dt, RiskEvent.created_at <= end_dt)
        .group_by(RiskEvent.layer)
        .order_by(RiskEvent.layer)
    ).all()
    layer_dist = [{"layer": r[0], "count": r[1]} for r in layer_rows]

    # ---- 5) 按日趋势（最近 N 天）----
    daily_rows = db.execute(
        select(
            func.date(RiskEvent.created_at).label("day"),
            func.count(RiskEvent.id).label("cnt"),
        )
        .where(RiskEvent.created_at >= start_dt, RiskEvent.created_at <= end_dt)
        .group_by(func.date(RiskEvent.created_at))
        .order_by(func.date(RiskEvent.created_at))
    ).all()
    daily_trend = [{"date": r[0], "count": r[1]} for r in daily_rows]

    # 误报率：风险事件里 action=allow 的比例（安全团队关注）
    allow_count = db.scalar(
        select(func.count(RiskEvent.id)).where(
            RiskEvent.created_at >= start_dt,
            RiskEvent.created_at <= end_dt,
            RiskEvent.action == "allow",
        )
    ) or 0
    false_positive_rate = round(allow_count / risk_total, 4) if risk_total else 0.0

    return {
        "period": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        },
        "summary": {
            "risk_events": risk_total,
            "blocked": blocked,
            "allowed": allow_count,
            "sessions": sessions_total,
            "audit_logs": logs_total,
            "false_positive_rate": false_positive_rate,
        },
        "top_rules": top_rules,
        "source_distribution": source_dist,
        "layer_distribution": layer_dist,
        "daily_trend": daily_trend,
    }


def export_csv(report: dict[str, Any]) -> str:
    """把报表导出为 CSV（含 summary + top_rules + daily_trend 三个区块）。"""
    buf = io.StringIO()
    w = csv.writer(buf)

    # 汇总
    w.writerow(["# AgentSoc 审计报表"])
    w.writerow(["周期", report["period"]["start"], "至", report["period"]["end"]])
    w.writerow([])
    w.writerow(["指标", "数值"])
    for k, v in report["summary"].items():
        w.writerow([k, v])

    # TOP 规则
    w.writerow([])
    w.writerow(["命中规则 TOP", "次数"])
    for r in report["top_rules"]:
        w.writerow([r["rule_id"], r["count"]])

    # 分层分布
    w.writerow([])
    w.writerow(["层级", "次数"])
    for r in report["layer_distribution"]:
        w.writerow([r["layer"], r["count"]])

    # 日趋势
    w.writerow([])
    w.writerow(["日期", "风险事件数"])
    for r in report["daily_trend"]:
        w.writerow([r["date"], r["count"]])

    return buf.getvalue()


def export_json(report: dict[str, Any]) -> str:
    """报表导出为 JSON 字符串。"""
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


__all__ = ["build_report", "export_csv", "export_json", "_parse_time"]
