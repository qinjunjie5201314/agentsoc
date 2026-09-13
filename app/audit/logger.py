"""D1 审计日志落库 —— 把每次检测/工具调用写入三张表。

设计原则
--------
1. **审计不能反噬主链路**：落库失败只记 warning，绝不 raise，绝不阻塞拦截/放行决策。
2. **异步 + 批量**：后台线程 + 队列，把高 QPS 的 DB 写操作从请求路径剥离。
3. **可回放**：``sessions`` / ``risk_events`` / ``audit_logs`` 三表按 ``session_id`` 关联，
   事后可完整还原「某次会话里发生了什么、哪条被拦、策略是哪个版本」。
4. **带策略版本**：每条 risk_event 都存 ``policy_version`` / ``policy_revision``（C3 已给检测结果
   铺垫这两个字段），可回答"这条拦截是哪版策略做出的"。

用法
----
    from app.audit.logger import AuditLogger
    audit = AuditLogger()
    audit.log_detection(session_id="...", result=result, messages=messages)
    audit.log_tool_call(session_id="...", tool_name="bash", decision=..., ...)
    ...
    audit.shutdown()  # 进程退出前 flush
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.audit.models import (
    AuditEventType,
    AuditLog,
    RiskAction,
    RiskEvent,
    RiskLevel,
    Session,
    SourceType,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_preview(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对 messages 做 L1 归一化预览（原始 → 归一化），供 D3 回放展示。

    与 ``app.proxy.openai_proxy._l1_preview`` 语义一致，但独立于此，避免循环导入。
    """
    try:
        from app.detection.isolate import tag_messages
        from app.detection.normalize import normalize
    except ImportError:  # pragma: no cover
        return []

    out: list[dict[str, Any]] = []
    for t in tag_messages(messages):
        content = getattr(t, "content", "")
        source = getattr(t, "source", None)
        source_v = source.value if source is not None else "unknown"
        try:
            norm = normalize(content) if content else content
        except Exception:  # noqa: BLE001 - 归一化失败不阻塞审计
            norm = content
        out.append({
            "source": source_v,
            "original": (content or "")[:500],
            "normalized": (norm or "")[:500],
            "changed": norm != content,
        })
    return out


# =========================== 队列事件 ===========================


@dataclass
class AuditRecord:
    """一条待落库的审计记录（轻量、可序列化）。"""

    session_id: str
    event_type: str          # AuditEventType.value
    payload: dict[str, Any] = field(default_factory=dict)
    # risk_event 专用字段
    layer: str | None = None
    rule_id: str | None = None
    risk_level: str | None = None
    action: str | None = None
    input_snippet: str | None = None
    matched: dict[str, Any] | None = None
    source: str | None = None
    normalized: dict[str, Any] | None = None
    policy_version: str | None = None
    policy_revision: int | None = None
    created_at: datetime = field(default_factory=_now)


# =========================== 主 logger ===========================


class AuditLogger:
    """异步审计落库器（进程级单例）。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] | None = None,
        maxsize: int = 10_000,
        flush_interval: float = 0.5,
        enabled: bool | None = None,
    ) -> None:
        # 延迟导入避免循环依赖
        from app.db import SessionLocal, session_scope

        self._session_factory = session_factory or SessionLocal
        self._session_scope = session_scope
        self._queue: queue.Queue[AuditRecord] = queue.Queue(maxsize=maxsize)
        self._flush_interval = flush_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = enabled if enabled is not None else True
        # 统计（供 /v1/audit 状态查询）
        self._dropped = 0
        self._written = 0
        self._lock = threading.Lock()

    # ---- 生命周期 ----

    def start(self) -> "AuditLogger":
        """启动后台 flush 线程。"""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._flush_loop, name="audit-logger", daemon=True,
        )
        self._thread.start()
        logger.info("AuditLogger 已启动 · enabled=%s", self._enabled)
        return self

    def shutdown(self, *, timeout: float = 3.0) -> None:
        """停止并 flush 剩余队列。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # 兜底：线程没来得及 flush 的，同步刷一次
        self._drain(force=True)
        logger.info("AuditLogger 已停止 · written=%d · dropped=%d", self._written, self._dropped)

    # ---- 写入接口 ----

    def log(
        self,
        session_id: str,
        event_type: AuditEventType | str,
        *,
        payload: dict[str, Any] | None = None,
        source: SourceType | str | None = None,
        layer: str | None = None,
        rule_id: str | None = None,
        risk_level: RiskLevel | str | None = None,
        action: RiskAction | str | None = None,
        input_snippet: str | None = None,
        matched: dict[str, Any] | None = None,
        normalized: dict[str, Any] | None = None,
        policy_version: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        """入队一条审计记录。"""
        if not self._enabled:
            return
        rec = AuditRecord(
            session_id=session_id,
            event_type=event_type.value if isinstance(event_type, AuditEventType) else event_type,
            payload=payload or {},
            source=source.value if isinstance(source, SourceType) else source,
            layer=layer,
            rule_id=rule_id,
            risk_level=risk_level.value if isinstance(risk_level, RiskLevel) else risk_level,
            action=action.value if isinstance(action, RiskAction) else action,
            input_snippet=input_snippet,
            matched=matched,
            normalized=normalized,
            policy_version=policy_version,
            policy_revision=policy_revision,
        )
        try:
            self._queue.put_nowait(rec)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            logger.warning("审计队列已满，丢弃一条（dropped=%d）", self._dropped)

    def log_detection(
        self,
        *,
        session_id: str,
        result: Any,
        messages: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
        agent_id: str = "default",
    ) -> None:
        """把一次 L1-L3 检测结果落库（含 risk_event + request audit_log）。"""
        # 1) session 主表（幂等 upsert）
        self._ensure_session(session_id, user_id=user_id, agent_id=agent_id)
        # 2) request 审计日志（含 L1 归一化预览，供 D3 回放展示"原始 → 归一化"）
        normalized_preview = _normalize_preview(messages or [])
        self.log(
            session_id,
            AuditEventType.REQUEST,
            payload={"messages": messages or [], "l1_preview": normalized_preview},
            normalized=normalized_preview,
            source=SourceType.USER,
            policy_version=getattr(result, "policy_version", None),
            policy_revision=getattr(result, "policy_revision", None),
        )
        # 3) 每个 hit 一条 risk_event
        for h in getattr(result, "hits", []) or []:
            self.log(
                session_id,
                AuditEventType.REQUEST,  # risk_event 复用 layer 区分；这里 event_type 仅占位
                layer=getattr(h, "layer", None) or "L3",
                rule_id=getattr(h, "rule_id", None),
                risk_level=getattr(h, "severity", None) or getattr(result, "risk_level", RiskLevel.LOW),
                action=getattr(result, "final_action", RiskAction.ALLOW),
                input_snippet=(getattr(h, "matched_text", None) or "")[:500],
                matched={"source": getattr(h, "source", None), "tags": getattr(h, "tags", None)},
                policy_version=getattr(result, "policy_version", None),
                policy_revision=getattr(result, "policy_revision", None),
            )
        # 无 hit 也记一条低危事件，保证会话至少有一条 risk 记录可查
        if not getattr(result, "hits", None):
            self.log(
                session_id,
                AuditEventType.REQUEST,
                layer="L1+L2+L3",
                risk_level=getattr(result, "risk_level", RiskLevel.LOW),
                action=getattr(result, "final_action", RiskAction.ALLOW),
                policy_version=getattr(result, "policy_version", None),
                policy_revision=getattr(result, "policy_revision", None),
            )

    def log_tool_call(
        self,
        *,
        session_id: str,
        tool_name: str,
        arguments: Any,
        status: str,          # executed / blocked / failed
        reason: str = "",
        dry_run: bool = True,
        policy_version: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        """把一次 L4 工具调用结果落库（tool_call audit_log + 可选 risk_event）。"""
        self.log(
            session_id,
            AuditEventType.TOOL_CALL,
            source=SourceType.TOOL,
            payload={"tool": tool_name, "arguments": arguments, "status": status, "reason": reason, "dry_run": dry_run},
            policy_version=policy_version,
            policy_revision=policy_revision,
        )
        # 被拦的工具调用 → 额外一条 risk_event
        if status == "blocked":
            self.log(
                session_id,
                AuditEventType.TOOL_CALL,
                layer="L4",
                rule_id=f"tool:{tool_name}",
                risk_level=RiskLevel.HIGH,
                action=RiskAction.BLOCK,
                input_snippet=reason[:500],
                matched={"tool": tool_name, "arguments": arguments},
                policy_version=policy_version,
                policy_revision=policy_revision,
            )

    def log_policy_reload(
        self,
        *,
        session_id: str = "",
        trigger: str = "",
        ok: bool = True,
        revision: int | None = None,
        version: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        """策略热更新事件。"""
        self.log(
            session_id or "policy",
            AuditEventType.POLICY_RELOAD,
            payload={"trigger": trigger, "ok": ok, "errors": errors or []},
            policy_version=version,
            policy_revision=revision,
        )

    # ---- 内部 ----

    def _ensure_session(self, session_id: str, *, user_id: str | None, agent_id: str) -> None:
        """幂等创建 session 主表记录。"""
        db = None
        try:
            db = self._session_factory()
            existing = db.get(Session, session_id)
            if existing is None:
                db.add(Session(
                    id=session_id,
                    user_id=user_id or "anonymous",
                    agent_id=agent_id,
                    started_at=_now(),
                    meta={},
                ))
                db.commit()
        except Exception as exc:  # noqa: BLE001 - 审计主表失败不反噬
            if db is not None:
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            logger.warning("创建 session 失败（忽略）: %s", exc)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass

    def _drain(self, *, force: bool = False) -> None:
        """批量落库队列中的所有记录。"""
        if self._queue.empty():
            return
        batch: list[AuditRecord] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return
        db = None
        try:
            db = self._session_factory()
        except Exception as exc:  # noqa: BLE001 - 审计失败不反噬主链路
            with self._lock:
                self._dropped += len(batch)
            logger.warning("审计落库失败（factory 不可用，丢弃 %d 条）: %s", len(batch), exc)
            return
        try:
            for rec in batch:
                self._write_one(db, rec)
            db.commit()
            with self._lock:
                self._written += len(batch)
        except Exception as exc:  # noqa: BLE001 - 审计失败不反噬主链路
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("审计批量落库失败（丢弃 %d 条）: %s", len(batch), exc)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass

    def _write_one(self, db: Any, rec: AuditRecord) -> None:
        """把单条记录写入对应表。"""
        # risk_event（layer 非空即写入）
        if rec.layer:
            db.add(RiskEvent(
                session_id=rec.session_id,
                layer=rec.layer,
                rule_id=rec.rule_id,
                risk_level=RiskLevel(rec.risk_level or "low"),
                action=RiskAction(rec.action or "allow"),
                input_snippet=rec.input_snippet,
                matched={
                    **(rec.matched or {}),
                    "policy_version": rec.policy_version,
                    "policy_revision": rec.policy_revision,
                } if (rec.policy_version or rec.policy_revision is not None) else rec.matched,
                created_at=rec.created_at,
            ))
        # audit_log（所有事件都写）
        db.add(AuditLog(
            session_id=rec.session_id,
            event_type=AuditEventType(rec.event_type),
            source=SourceType(rec.source) if rec.source else None,
            payload=rec.payload,
            normalized=rec.normalized,
            created_at=rec.created_at,
        ))

    def _flush_loop(self) -> None:
        """后台线程：定时 drain。"""
        while not self._stop.is_set():
            self._drain()
            self._stop.wait(self._flush_interval)
        self._drain(force=True)

    # ---- 查询统计 ----

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "queue_size": self._queue.qsize(),
                "written": self._written,
                "dropped": self._dropped,
            }


__all__ = ["AuditLogger", "AuditRecord"]
