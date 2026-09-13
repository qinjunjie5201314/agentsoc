"""工具执行器 —— C2 用。

M1 只实现 ``DryRunToolExecutor``：**绝不真执行任何副作用操作**，只返回结构化
模拟结果。目的是把「L4 放行 → 工具执行 → 结果回灌给模型」这段链路跑通，
让 demo 能展示完整 agent loop。

生产环境（M2）替换为真实执行器（带沙箱、超时、资源限制），
本模块的 ``ToolExecutor`` 协议保持不变。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionResult:
    """一次工具执行的结果。"""

    name: str
    ok: bool
    output: Any = None
    error: str = ""
    dry_run: bool = True
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "dry_run": self.dry_run,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


class ToolExecutor(Protocol):
    """执行器协议（M2 可换成真实沙箱执行器）。"""

    def execute(self, name: str, arguments: Any) -> ToolExecutionResult: ...


# ---- 内置的安全工具模拟实现（dry-run） ----

_MOCK_OUTPUTS: dict[str, Any] = {
    "get_weather": lambda a: {
        "city": a.get("city", "unknown"),
        "temp_c": 24,
        "condition": "晴",
        "source": "mock-weather-api",
    },
    "search": lambda a: {
        "query": a.get("query", ""),
        "results": [
            {"title": f"关于 {a.get('query', '')} 的结果 1", "url": "https://example.com/1"},
            {"title": f"关于 {a.get('query', '')} 的结果 2", "url": "https://example.com/2"},
        ],
        "total": 2,
    },
    "read_file": lambda a: {
        "path": a.get("path", ""),
        "content": f"[dry-run] 这是 {a.get('path', '')} 的模拟内容（M1 不真读盘）",
        "size": 128,
    },
    "write_file": lambda a: {
        "path": a.get("path", ""),
        "bytes_written": len(str(a.get("content", ""))),
        "note": "[dry-run] 未真正写盘",
    },
    "send_email": lambda a: {
        "to": a.get("to", ""),
        "subject": a.get("subject", ""),
        "status": "queued",
        "note": "[dry-run] 未真正发信",
    },
    "query_database": lambda a: {
        "sql": a.get("sql", ""),
        "rows": [{"id": 1, "name": "sample"}],
        "row_count": 1,
        "note": "[dry-run] 未真正查库",
    },
}


class DryRunToolExecutor:
    """M1 沙箱执行器：只返回模拟结果，零副作用。"""

    name = "dry-run"

    def __init__(self, mock_outputs: dict[str, Any] | None = None) -> None:
        self._mocks = dict(_MOCK_OUTPUTS)
        if mock_outputs:
            self._mocks.update(mock_outputs)
        self.calls: list[ToolExecutionResult] = []

    def execute(self, name: str, arguments: Any) -> ToolExecutionResult:
        started = time.perf_counter()
        key = (name or "").lower()
        try:
            if key in self._mocks:
                args = arguments if isinstance(arguments, dict) else {}
                output = self._mocks[key](args)
                result = ToolExecutionResult(
                    name=name,
                    ok=True,
                    output=output,
                    dry_run=True,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            else:
                # 未注册工具：返回"未实现"，但仍然 ok=False 以提示调用方
                result = ToolExecutionResult(
                    name=name,
                    ok=False,
                    error=f"工具 {name!r} 未在 dry-run 执行器中注册（M1 仅支持内置安全工具）",
                    dry_run=True,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
        except Exception as exc:  # noqa: BLE001 - 执行器不能因单个工具崩掉
            result = ToolExecutionResult(
                name=name,
                ok=False,
                error=f"执行异常: {exc}",
                dry_run=True,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        self.calls.append(result)
        logger.info(
            "工具执行（dry-run）· %s · ok=%s · %.2fms",
            name,
            result.ok,
            result.elapsed_ms,
        )
        return result

    def execute_many(self, calls: list[tuple[str, Any]]) -> list[ToolExecutionResult]:
        return [self.execute(n, a) for n, a in calls]

    def reset(self) -> None:
        self.calls.clear()

    @staticmethod
    def as_text(result: ToolExecutionResult) -> str:
        """把结果转成可回灌给模型的字符串。"""
        if result.ok:
            return json.dumps(result.output, ensure_ascii=False)
        return json.dumps({"error": result.error}, ensure_ascii=False)


__all__ = [
    "DryRunToolExecutor",
    "ToolExecutionResult",
    "ToolExecutor",
]
