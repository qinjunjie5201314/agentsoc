"""C3 策略热更新的**目录监听器**。

双后端设计
----------
::

    backend="auto"（默认）
        ├─ watchdog 可用  → 事件驱动（Windows ReadDirectoryChangesW / Linux inotify）
        └─ watchdog 不可用 → 轮询（interval 秒一次）

**为什么还要保留轮询**：watchdog 在容器 bind-mount、部分网络盘上会丢事件；
轮询虽然笨但"绝不会漏"。所以事件后端也保留一个 ``interval`` 秒的兜底轮询，
两者互补：常规改动走事件（毫秒级生效），极端环境最坏退化成 interval 秒。

**为什么还要内容 hash 比对**：编辑器保存、DLP 透明加解密、touch 都会产生
"mtime 变了但内容没变"的事件。``PolicyRegistry.reload()`` 会做 sha256 比对，
内容真没变时返回 ``changed=False``，不会误 bump 版本号污染审计。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

if TYPE_CHECKING:  # pragma: no cover
    from app.policy.registry import PolicyRegistry, ReloadResult

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 取决于运行环境
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    FileSystemEventHandler = None  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]
    WATCHDOG_AVAILABLE = False

DirSignature = tuple[tuple[str, int, int], ...]
Backend = Literal["auto", "polling", "watchdog"]

_YAML_SUFFIXES = (".yaml", ".yml")


if WATCHDOG_AVAILABLE:

    class _YamlChangeHandler(FileSystemEventHandler):  # type: ignore[misc,valid-type]
        """只关心策略目录下 YAML 的增 / 删 / 改 / 移动。"""

        def __init__(self, signal: threading.Event) -> None:
            super().__init__()
            self._signal = signal

        def on_any_event(self, event: Any) -> None:
            for attr in ("src_path", "dest_path"):
                path = str(getattr(event, attr, "") or "").lower()
                if path.endswith(_YAML_SUFFIXES):
                    self._signal.set()
                    return


class PolicyWatcher:
    """后台线程监听策略目录，变化即触发 ``registry.reload()``。"""

    def __init__(
        self,
        registry: PolicyRegistry,
        *,
        interval: float = 2.0,
        on_change: Callable[[ReloadResult], None] | None = None,
        backend: Backend = "auto",
        name: str = "agentsentry-policy-watcher",
    ) -> None:
        self.registry = registry
        self.interval = max(0.05, float(interval))
        self.on_change = on_change
        self.backend: Backend = backend
        self.name = name

        self._thread: threading.Thread | None = None
        self._observer: Any = None
        self._stop = threading.Event()
        self._change = threading.Event()
        self._lock = threading.RLock()
        self._signature: DirSignature | None = None
        self._resolved_backend: str = "polling"

        # 统计（供可视化页展示"watcher 真的在跑"）
        self.started_at: float = 0.0
        self.polls: int = 0
        self.reloads: int = 0
        self.changes: int = 0
        self.events: int = 0
        self.last_poll_at: float = 0.0
        self.last_change_at: float = 0.0
        self.last_result: ReloadResult | None = None

    # ---- 生命周期 ----

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def resolved_backend(self) -> str:
        """实际生效的后端（auto 会在这里被解析成 polling / watchdog）。"""
        if self.running:
            return self._resolved_backend
        return self._resolve_backend()[0]

    def _resolve_backend(self) -> tuple[str, str]:
        """返回 (实际后端, 降级原因)。"""
        if self.backend == "polling":
            return "polling", ""
        if not Path(self.registry.policy_dir).is_dir():
            return "polling", f"策略目录不存在: {self.registry.policy_dir}"
        if not WATCHDOG_AVAILABLE:
            return "polling", "watchdog 未安装"
        return "watchdog", ""

    def start(self) -> Self:
        """启动监听（幂等）。"""
        with self._lock:
            if self.running:
                return self
            self._stop.clear()
            self._change.clear()
            self._signature = self._dir_signature()
            self.started_at = time.time()

            resolved, reason = self._resolve_backend()
            self._resolved_backend = resolved
            if reason and self.backend != "polling":
                logger.warning("watchdog 后端不可用（%s），降级为轮询", reason)

            if resolved == "watchdog":
                self._start_observer()
                resolved = self._resolved_backend  # observer 启动失败会降级

            target = self._watchdog_loop if resolved == "watchdog" else self._polling_loop
            self._thread = threading.Thread(target=target, name=self.name, daemon=True)
            self._thread.start()
            logger.info(
                "策略监听已启动 · backend=%s · dir=%s · interval=%.1fs · %d 个文件",
                resolved,
                self.registry.policy_dir,
                self.interval,
                len(self._signature),
            )
        return self

    def _start_observer(self) -> None:
        try:
            observer = Observer()
            observer.daemon = True
            observer.schedule(
                _YamlChangeHandler(self._change),
                str(self.registry.policy_dir),
                recursive=False,
            )
            observer.start()
            self._observer = observer
        # watchdog 在 inotify 上限、权限不足、不支持的文件系统上会抛各色异常，
        # 这里必须兜住全部异常并降级为轮询，否则整个监听会失效。
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - 环境相关
            logger.warning("watchdog observer 启动失败，降级为轮询: %s", exc)
            self._observer = None
            self._resolved_backend = "polling"

    def stop(self, timeout: float = 5.0) -> None:
        """停止监听（幂等）。"""
        with self._lock:
            thread = self._thread
            observer = self._observer
            self._stop.set()
            self._change.set()  # 唤醒等待中的循环
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=timeout)
            except Exception:  # pragma: no cover - observer 已自行退出
                logger.debug("watchdog observer 停止时异常（已忽略）", exc_info=True)
        with self._lock:
            self._thread = None
            self._observer = None
        logger.info("策略监听已停止")

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---- 循环 ----

    def _polling_loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._safe_poll()

    def _watchdog_loop(self) -> None:
        """事件驱动；``interval`` 秒无事件时也兜底轮询一次，绝不漏改动。"""
        while not self._stop.is_set():
            signaled = self._change.wait(self.interval)
            if self._stop.is_set():
                break
            if signaled:
                self._change.clear()
                with self._lock:
                    self.events += 1
            self._safe_poll()

    def _safe_poll(self) -> None:
        try:
            self.poll_once()
        except Exception:  # pragma: no cover - 守护线程绝不能因异常退出
            logger.exception("策略监听轮询异常（已忽略，继续监听）")

    # ---- 核心 ----

    def _dir_signature(self) -> DirSignature:
        sig: list[tuple[str, int, int]] = []
        for p in self.registry.scan_files():
            try:
                st = p.stat()
            except OSError:
                continue
            sig.append((p.name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(sig))

    def poll_once(self, *, force: bool = False) -> ReloadResult | None:
        """同步执行一次检查（测试与手动触发都走这里）。

        Returns:
            发生 reload 时返回 ReloadResult；目录指纹未变返回 None。
        """
        signature = self._dir_signature()
        with self._lock:
            self.polls += 1
            self.last_poll_at = time.time()
            changed = signature != self._signature

        if not changed and not force:
            return None

        with self._lock:
            self._signature = signature

        result = self.registry.reload(trigger="watcher", force=force)
        with self._lock:
            self.reloads += 1
            self.last_result = result
            if result.changed or result.errors:
                self.changes += 1
                self.last_change_at = time.time()

        if result.changed:
            logger.info("检测到策略文件变化 · %s", result.summary())
        elif result.errors:
            logger.error("检测到策略文件变化但加载失败 · %s", result.summary())
        if self.on_change is not None and (result.changed or result.errors):
            try:
                self.on_change(result)
            except Exception:  # pragma: no cover - 回调异常不影响监听
                logger.exception("on_change 回调失败")
        return result

    # ---- 状态 ----

    @property
    def backend_label(self) -> str:
        """给可视化用的后端描述，如 ``watchdog+polling(2.0s)``。"""
        resolved = self.resolved_backend
        if resolved == "watchdog":
            return f"watchdog+polling({self.interval:.1f}s)"
        return f"polling({self.interval:.1f}s)"

    def status(self) -> dict[str, Any]:
        from app.policy.registry import _iso  # 局部导入避免循环依赖

        with self._lock:
            return {
                "running": self.running,
                "backend": self._resolved_backend,
                "backend_label": self.backend_label,
                "watchdog_available": WATCHDOG_AVAILABLE,
                "requested_backend": self.backend,
                "interval_s": self.interval,
                "policy_dir": str(self.registry.policy_dir),
                "watched_files": [name for name, _, _ in (self._signature or ())],
                "started_at": self.started_at,
                "started_at_iso": _iso(self.started_at),
                "polls": self.polls,
                "reloads": self.reloads,
                "changes": self.changes,
                "events": self.events,
                "last_poll_at": self.last_poll_at,
                "last_poll_at_iso": _iso(self.last_poll_at),
                "last_change_at": self.last_change_at,
                "last_change_at_iso": _iso(self.last_change_at),
                "last_result": self.last_result.to_dict() if self.last_result else None,
            }

    def watched_files(self) -> list[str]:
        return [p.name for p in self.registry.scan_files()]


def watch_dir_has_yaml(path: str | Path) -> bool:
    """辅助：目录里是否存在 YAML 策略文件。"""
    p = Path(path)
    return p.is_dir() and any(p.glob("*.y*ml"))


__all__ = ["WATCHDOG_AVAILABLE", "PolicyWatcher", "watch_dir_has_yaml"]
