"""D2 终端日志展示 —— 实时风险事件流。

用法：
    python -m app.cli.dashboard            # 实时刷新（默认 2s）
    python -m app.cli.dashboard --once     # 只打印一次快照
    python -m app.cli.dashboard --limit 20 # 显示最近 20 条
    python -m app.cli.dashboard --db sqlite:///./agentsentry.db  # 指定库

读取 D1 落库的 ``risk_events`` / ``audit_logs`` / ``sessions`` 三表，
用 Rich ``Live`` 表格实时渲染，供运维/安全团队在终端盯风险事件。

设计：只读，绝不写入；DB 不可用时给出友好提示而不是崩溃。
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from app.audit.models import AuditLog, RiskEvent, Session

console = Console()


def _load(db_url: str) -> "tuple[sqlalchemy.engine.Engine, object]":
    """构造 engine + SessionLocal（延迟导入，避免 import 时连库）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
        echo=False,
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _fmt(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone().strftime("%H:%M:%S")


def _risk_table(events: list[RiskEvent]) -> Table:
    t = Table(show_header=True, header_style="bold", pad_edge=False)
    t.add_column("时间", style="dim", width=9)
    t.add_column("层级", width=10)
    t.add_column("风险", width=8)
    t.add_column("动作", width=8)
    t.add_column("规则", overflow="fold", max_width=30)
    t.add_column("命中片段", overflow="fold", max_width=36)
    t.add_column("会话", overflow="fold", max_width=18)

    sev_style = {"high": "bold red", "medium": "yellow", "low": "green"}
    act_style = {"block": "bold red", "confirm": "yellow", "allow": "green"}
    for e in events:
        t.add_row(
            _fmt(e.created_at),
            e.layer or "—",
            f"[{sev_style.get(e.risk_level.value, 'white')}]{e.risk_level.value}[/]",
            f"[{act_style.get(e.action.value, 'white')}]{e.action.value}[/]",
            e.rule_id or "—",
            (e.input_snippet or "")[:80],
            (e.session_id or "")[:24],
        )
    return t


def _summary_table(
    total_events: int,
    total_sessions: int,
    block_count: int,
    logs_count: int,
) -> Table:
    t = Table(show_header=False, pad_edge=False)
    t.add_column("指标", style="bold", width=16)
    t.add_column("值")
    t.add_row("风险事件总数", f"[bold]{total_events}[/]")
    t.add_row("会话总数", f"[bold]{total_sessions}[/]")
    t.add_row("被拦截（block）", f"[bold red]{block_count}[/]")
    t.add_row("审计日志总数", f"[bold]{logs_count}[/]")
    return t


def snapshot(db_url: str, limit: int) -> None:
    """打印一次快照。"""
    engine, SessionLocal = _load(db_url)
    from sqlalchemy import func, select

    with SessionLocal() as db:
        try:
            total_events = db.query(func.count(RiskEvent.id)).scalar() or 0
            total_sessions = db.query(func.count(Session.id)).scalar() or 0
            block_count = (
                db.query(func.count(RiskEvent.id))
                .filter(RiskEvent.action == "block")
                .scalar()
                or 0
            )
            logs_count = db.query(func.count(AuditLog.id)).scalar() or 0
            events = (
                db.query(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit).all()
            )
        except Exception as exc:  # noqa: BLE001
            console.print(Panel(f"[red]读取数据库失败：{exc}[/]", border_style="red"))
            return

    console.print(Panel("AgentSoc · 风险事件流", style="blue"))
    console.print(_summary_table(total_events, total_sessions, block_count, logs_count))
    console.print("")
    console.print(_risk_table(list(events)))


def watch(db_url: str, limit: int, interval: float) -> None:
    """实时刷新。"""
    engine, SessionLocal = _load(db_url)
    from rich.console import Group
    from sqlalchemy import func

    def _full() -> Group | Panel:
        with SessionLocal() as db:
            try:
                total_events = db.query(func.count(RiskEvent.id)).scalar() or 0
                total_sessions = db.query(func.count(Session.id)).scalar() or 0
                block_count = (
                    db.query(func.count(RiskEvent.id))
                    .filter(RiskEvent.action == "block")
                    .scalar()
                    or 0
                )
                events = (
                    db.query(RiskEvent)
                    .order_by(RiskEvent.created_at.desc())
                    .limit(limit)
                    .all()
                )
                logs_count = db.query(func.count(AuditLog.id)).scalar() or 0
            except Exception as exc:  # noqa: BLE001
                return Panel(f"[red]读取数据库失败：{exc}[/]", border_style="red")

        head = Panel("AgentSoc · 实时风险事件流（Ctrl+C 退出）", style="blue")
        summ = _summary_table(total_events, total_sessions, block_count, logs_count)
        table = _risk_table(list(events))
        return Group(head, summ, table)

    try:
        with Live(_full(), console=console, refresh_per_second=4) as live:
            while True:
                time.sleep(interval)
                live.update(_full())
    except KeyboardInterrupt:
        console.print("\n[dim]已停止。[/]")


def main() -> None:
    from app.config import settings

    parser = argparse.ArgumentParser(description="AgentSoc 终端日志展示")
    parser.add_argument("--db", default=settings.database_url, help="数据库 URL")
    parser.add_argument("--once", action="store_true", help="只打印一次快照")
    parser.add_argument("--limit", type=int, default=20, help="显示最近 N 条")
    parser.add_argument("--interval", type=float, default=2.0, help="刷新间隔（秒）")
    args = parser.parse_args()

    if args.once:
        snapshot(args.db, args.limit)
    else:
        watch(args.db, args.limit, args.interval)


if __name__ == "__main__":
    main()
