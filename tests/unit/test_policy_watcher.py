"""C3 单元测试：PolicyWatcher 目录监听与热更新触发。

策略：不依赖 sleep 时序判成败，能同步调的就用 ``poll_once()``；
必须验证后台线程时，用"带 deadline 的轮询等待"而非固定 sleep。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.policy import PolicyRegistry, PolicyWatcher
from app.policy.watcher import WATCHDOG_AVAILABLE, watch_dir_has_yaml

RULES_YAML = """
rules:
  - id: rule_a
    description: 规则 A
    severity: high
    patterns:
      - type: keyword_any
        value: [attack_marker]
"""


def _write(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


def _wait_until(predicate, timeout: float = 4.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _start_sync_watcher(registry: PolicyRegistry) -> PolicyWatcher:
    """只接受手动 ``poll_once()`` 的 watcher。

    用 polling 后端 + 超长间隔，保证后台线程不会在测试断言前把变更"吃掉"，
    让 ``poll_once`` 的结果完全确定。
    """
    return PolicyWatcher(registry, interval=60.0, backend="polling").start()


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    d = tmp_path / "policies"
    d.mkdir()
    _write(d, "rules.yaml", RULES_YAML)
    return d


@pytest.fixture
def registry(policy_dir: Path) -> PolicyRegistry:
    reg = PolicyRegistry(policy_dir)
    reg.reload(trigger="startup")
    return reg


# ============ 生命周期 ============


def test_start_stop(registry: PolicyRegistry) -> None:
    w = PolicyWatcher(registry, interval=0.05)
    assert w.running is False
    w.start()
    assert w.running is True
    w.stop()
    assert w.running is False
    assert w._thread is None


def test_start_is_idempotent(registry: PolicyRegistry) -> None:
    w = PolicyWatcher(registry, interval=0.05)
    w.start()
    thread = w._thread
    w.start()
    assert w._thread is thread
    w.stop()


def test_context_manager(registry: PolicyRegistry) -> None:
    with PolicyWatcher(registry, interval=0.05) as w:
        assert w.running is True
    assert w.running is False


def test_stop_without_start_is_safe(registry: PolicyRegistry) -> None:
    PolicyWatcher(registry).stop()  # 不抛异常


# ============ poll_once ============


def test_poll_once_returns_none_when_unchanged(registry: PolicyRegistry) -> None:
    w = _start_sync_watcher(registry)
    try:
        assert w.poll_once() is None
        assert w.polls == 1
        assert w.reloads == 0
    finally:
        w.stop()


def test_poll_once_detects_content_change(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = _start_sync_watcher(registry)
    try:
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# v2\n")
        result = w.poll_once()
        assert result is not None
        assert result.changed is True
        assert result.trigger == "watcher"
        assert registry.revision == 2
        assert w.reloads == 1
        assert w.changes == 1
        assert w.last_change_at > 0
    finally:
        w.stop()


def test_poll_once_touch_without_content_change(registry: PolicyRegistry, policy_dir: Path) -> None:
    """mtime 变了但内容没变 → 触发一次 reload，但版本不动（内容寻址兜底）。"""
    w = _start_sync_watcher(registry)
    try:
        path = policy_dir / "rules.yaml"
        path.touch()
        result = w.poll_once()
        assert result is not None
        assert result.changed is False
        assert registry.revision == 1
    finally:
        w.stop()


def test_poll_once_force(registry: PolicyRegistry) -> None:
    w = _start_sync_watcher(registry)
    try:
        result = w.poll_once(force=True)
        assert result is not None
        assert result.changed is True
        assert registry.revision == 2
    finally:
        w.stop()


def test_poll_detects_new_file(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = _start_sync_watcher(registry)
    try:
        _write(policy_dir, "extra.yaml", "blocked_tools: [curl]\n")
        result = w.poll_once()
        assert result is not None and result.changed is True
        assert result.added_files == ("extra.yaml",)
        assert registry.tool_policy.blocked_tools == ["curl"]
    finally:
        w.stop()


def test_poll_detects_file_removal(registry: PolicyRegistry, policy_dir: Path) -> None:
    _write(policy_dir, "extra.yaml", "blocked_tools: [curl]\n")
    registry.reload()
    w = _start_sync_watcher(registry)
    try:
        (policy_dir / "extra.yaml").unlink()
        result = w.poll_once()
        assert result is not None and result.changed is True
        assert result.removed_files == ("extra.yaml",)
        assert registry.tool_policy.blocked_tools == []
    finally:
        w.stop()


# ============ 后台线程 ============


def test_background_thread_picks_up_change(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = PolicyWatcher(registry, interval=0.05).start()
    try:
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# background\n")
        assert _wait_until(lambda: registry.revision == 2), "监听线程未在 4s 内感知变更"
        assert w.changes >= 1
    finally:
        w.stop()


def test_background_thread_survives_broken_yaml(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = PolicyWatcher(registry, interval=0.05).start()
    try:
        _write(policy_dir, "rules.yaml", "rules: [\n")
        assert _wait_until(lambda: w.last_result is not None and not w.last_result.ok)
        # 线程还在，策略还是旧的
        assert w.running is True
        assert registry.revision == 1
        assert registry.snapshot.rule_count == 1

        # 修好后能恢复
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# fixed\n")
        assert _wait_until(lambda: registry.revision == 2)
    finally:
        w.stop()


def test_on_change_callback(registry: PolicyRegistry, policy_dir: Path) -> None:
    seen: list[bool] = []
    w = PolicyWatcher(registry, interval=0.05, on_change=lambda r: seen.append(r.changed)).start()
    try:
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# cb\n")
        assert _wait_until(lambda: seen == [True])
    finally:
        w.stop()


def test_on_change_callback_exception_is_swallowed(
    registry: PolicyRegistry, policy_dir: Path
) -> None:
    def boom(_result):  # type: ignore[no-untyped-def]
        raise RuntimeError("callback boom")

    w = PolicyWatcher(registry, interval=0.05, on_change=boom).start()
    try:
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# boom\n")
        assert _wait_until(lambda: registry.revision == 2)
        assert w.running is True
    finally:
        w.stop()


# ============ status ============


def test_status_shape(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = PolicyWatcher(registry, interval=0.25, backend="polling")
    w.start()
    try:
        st = w.status()
        assert st["running"] is True
        assert st["backend"] == "polling"
        assert st["requested_backend"] == "polling"
        assert st["backend_label"] == "polling(0.2s)"
        assert st["interval_s"] == 0.25
        assert st["policy_dir"] == str(policy_dir)
        assert st["watched_files"] == ["rules.yaml"]
        assert st["started_at_iso"]
        assert st["last_result"] is None
    finally:
        w.stop()


def test_backend_auto_uses_watchdog_when_available(registry: PolicyRegistry) -> None:
    w = PolicyWatcher(registry, interval=0.05)
    if not WATCHDOG_AVAILABLE:
        pytest.skip("watchdog 未安装")
    w.start()
    try:
        assert w.status()["backend"] == "watchdog"
        assert w.status()["backend_label"] == "watchdog+polling(0.1s)"
        assert w.status()["watchdog_available"] is True
    finally:
        w.stop()


def test_backend_downgrades_when_policy_dir_missing(tmp_path: Path) -> None:
    reg = PolicyRegistry(tmp_path / "ghost")
    reg.reload()
    w = PolicyWatcher(reg, interval=0.05, backend="auto")
    assert w.resolved_backend == "polling"
    w.start()
    try:
        assert w.status()["backend"] == "polling"
    finally:
        w.stop()


@pytest.mark.skipif(not WATCHDOG_AVAILABLE, reason="watchdog 未安装")
def test_watchdog_backend_reacts_to_event_without_waiting_for_poll(
    registry: PolicyRegistry, policy_dir: Path
) -> None:
    """事件驱动路径：把兜底轮询调到 30s，仍应在 3s 内感知改动。"""
    w = PolicyWatcher(registry, interval=30.0).start()
    try:
        assert w.status()["backend"] == "watchdog"
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# event-driven\n")
        assert _wait_until(lambda: registry.revision == 2, timeout=8.0), "事件后端未感知到改动"
        assert w.events >= 1
    finally:
        w.stop()


def test_interval_floor_and_backend_label(registry: PolicyRegistry) -> None:
    w = PolicyWatcher(registry, interval=0.05, backend="polling")
    assert w.interval == 0.05
    assert w.backend_label == "polling(0.1s)"
    w.start()
    try:
        assert w.status()["backend"] == "polling"
    finally:
        w.stop()


def test_status_after_change(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = _start_sync_watcher(registry)
    try:
        _write(policy_dir, "rules.yaml", RULES_YAML + "\n# st\n")
        w.poll_once()
        st = w.status()
        assert st["reloads"] >= 1
        assert st["last_result"]["changed"] is True
        assert st["last_change_at_iso"]
    finally:
        w.stop()


def test_watched_files(registry: PolicyRegistry, policy_dir: Path) -> None:
    w = PolicyWatcher(registry)
    assert w.watched_files() == ["rules.yaml"]
    _write(policy_dir, "b.yaml", "blocked_tools: [x]\n")
    assert w.watched_files() == ["b.yaml", "rules.yaml"]


def test_watch_dir_has_yaml(tmp_path: Path) -> None:
    assert watch_dir_has_yaml(tmp_path) is False
    _write(tmp_path, "a.yml", "rules: []\n")
    assert watch_dir_has_yaml(tmp_path) is True


def test_watcher_sees_reregistration_of_registry_dir(registry: PolicyRegistry) -> None:
    """registry 目录不存在时也不应崩溃。"""
    reg = PolicyRegistry(registry.policy_dir / "ghost")
    reg.reload()
    w = PolicyWatcher(reg, interval=0.05).start()
    try:
        assert w.poll_once() is None
        assert w.running is True
    finally:
        w.stop()
