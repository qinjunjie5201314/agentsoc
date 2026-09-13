"""监控指标 —— 轻量 Prometheus 文本格式（零第三方依赖）。

设计：进程内计数器，Prometheus 格式输出。指标：
  - agentsoc_requests_total{action}      请求总量（按最终动作 allow/block/confirm 分）
  - agentsoc_detection_ms                 检测耗时（histogram 简化为 sum/count）
  - agentsoc_classifier_ms{provider}      判别模型推理耗时
  - agentsoc_tool_calls_total{status}     工具调用（executed/blocked/failed）
  - agentsoc_policy_reloads_total         策略热更新次数
  - agentsoc_audit_written_total          审计落库条数

线程安全：用 threading.Lock 保护，避免并发计数竞争。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class Metrics:
    """进程内指标注册表（单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._sums: dict[str, float] = defaultdict(float)
        self._counts: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        self._started_at = time.time()

    # ---- 计数 ----

    def incr(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """histogram 简化为 sum + count。"""
        key = self._key(name, labels)
        with self._lock:
            self._sums[key] += value
            self._counts[key] += 1

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    # ---- 便捷封装（业务语义）----

    def record_request(self, action: str, elapsed_ms: float) -> None:
        self.incr("agentsoc_requests_total", {"action": action})
        self.observe("agentsoc_detection_ms", elapsed_ms)

    def record_classifier(self, provider: str, elapsed_ms: float) -> None:
        self.observe("agentsoc_classifier_ms", elapsed_ms, {"provider": provider})

    def record_tool_call(self, status: str) -> None:
        self.incr("agentsoc_tool_calls_total", {"status": status})

    def record_policy_reload(self) -> None:
        self.incr("agentsoc_policy_reloads_total")

    def record_audit_written(self, count: int) -> None:
        self.incr("agentsoc_audit_written_total", value=count)

    # ---- 渲染 ----

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        # 标签按字母序排序，保证 key 稳定
        parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{parts}}}"

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        """把 'name{k="v"}' 拆成 (name, '{k="v"}')。"""
        if "{" not in key:
            return key, ""
        name, _, labels = key.partition("{")
        return name, "{" + labels

    def render(self) -> str:
        """输出 Prometheus 文本格式。"""
        lines: list[str] = []
        # 计数器
        for key, val in sorted(self._counters.items()):
            name, labels = self._split_key(key)
            lines.append(f"{name}{labels} {_fmt(val)}")
        # histogram（sum + count）
        for key in sorted(self._sums.keys()):
            name, labels = self._split_key(key)
            lines.append(f"{name}_sum{labels} {_fmt(self._sums[key])}")
            lines.append(f"{name}_count{labels} {_fmt(self._counts[key])}")
        # gauge
        for key, val in sorted(self._gauges.items()):
            name, labels = self._split_key(key)
            lines.append(f"{name}{labels} {_fmt(val)}")
        # 进程运行时长
        lines.append(f"agentsoc_uptime_seconds {_fmt(time.time() - self._started_at)}")
        return "\n".join(lines) + "\n"


def _fmt(v: float) -> str:
    """数值格式化：整数不带小数点，浮点保留合理精度。"""
    if v == int(v):
        return str(int(v))
    return f"{v:.6f}".rstrip("0").rstrip(".")


# 全局单例
metrics = Metrics()

__all__ = ["Metrics", "metrics"]
