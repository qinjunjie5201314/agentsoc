"""C2 工具调用 Hook —— LLM 准备调工具时的强制 L4 兜底关卡。

与 C1 的区别：
  - C1 拦的是「输入文本」（prompt 注入 / 越狱），发生在**请求进入时**
  - C2 拦的是「模型决定要做的动作」（工具调用），发生在**响应返回后、工具执行前**

这是 Agent 场景最关键的一道闸门：Prompt 注入的最终危害几乎都要靠工具落地
（删库、外发数据、执行 shell）。只要在执行前卡住，注入就只剩"说废话"。

支持的协议：
  - **OpenAI**：``assistant.tool_calls[].function.{name,arguments}``（arguments 是 JSON 字符串）
  - **OpenAI legacy**：``assistant.function_call.{name,arguments}``
  - **Anthropic**：``content[].{type:tool_use, id, name, input}``

调用链：
  ``extract_tool_calls(payload)`` → ``ToolGuard.guard_calls(calls)`` → ``ToolGuardResult``
  → 被拦的用 ``build_tool_result_error()`` 转成 tool_result（回灌给模型，让它知道自己被拦）
  → 放行的交给 ToolExecutor 执行。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from app.audit.models import RiskAction, RiskLevel
from app.detection.egress import (
    EgressDecision,
    ToolPolicy,
    check_tool_call,
    load_tool_policy,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.policy.registry import PolicyRegistry

logger = logging.getLogger(__name__)


PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"


# ============ 数据结构 ============


@dataclass
class ExtractedToolCall:
    """从 LLM 响应里提取出的一次工具调用。"""

    call_id: str
    name: str
    arguments: Any  # 解析后的参数（dict / list / str）
    raw_arguments: str  # 原始字符串形式（OpenAI 是 JSON 字符串）
    protocol: str = PROTOCOL_OPENAI

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
            "raw_arguments": self.raw_arguments,
            "protocol": self.protocol,
        }


@dataclass
class GuardedToolCall:
    """一次工具调用 + 它的 L4 体检结论。"""

    call: ExtractedToolCall
    decision: EgressDecision

    @property
    def allowed(self) -> bool:
        return self.decision.passed

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.call.to_dict(),
            "allowed": self.allowed,
            "action": self.decision.action.value,
            "risk_level": self.decision.risk_level.value,
            "reason": self.decision.reason,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.decision.checks
            ],
        }


@dataclass
class ToolGuardResult:
    """一批工具调用的整体守卫结果。"""

    guarded: list[GuardedToolCall] = field(default_factory=list)

    @property
    def allowed(self) -> list[GuardedToolCall]:
        return [g for g in self.guarded if g.allowed]

    @property
    def blocked(self) -> list[GuardedToolCall]:
        return [g for g in self.guarded if g.blocked]

    @property
    def has_calls(self) -> bool:
        return bool(self.guarded)

    @property
    def all_blocked(self) -> bool:
        return bool(self.guarded) and not self.allowed

    @property
    def action(self) -> RiskAction:
        """整体动作：有任何一个被拦即 BLOCK（保守取向）。"""
        if not self.guarded:
            return RiskAction.ALLOW
        return RiskAction.BLOCK if self.blocked else RiskAction.ALLOW

    @property
    def risk_level(self) -> RiskLevel:
        if not self.guarded:
            return RiskLevel.LOW
        return max((g.decision.risk_level for g in self.guarded), key=_level_rank)

    @property
    def reason(self) -> str:
        if not self.blocked:
            return ""
        return "; ".join(f"[{g.call.name}] {g.decision.reason}" for g in self.blocked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.guarded),
            "allowed_count": len(self.allowed),
            "blocked_count": len(self.blocked),
            "action": self.action.value,
            "risk_level": self.risk_level.value,
            "reason": self.reason,
            "calls": [g.to_dict() for g in self.guarded],
        }


_LEVEL_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def _level_rank(level: RiskLevel) -> int:
    return _LEVEL_RANK.get(level, 0)


# ============ 工具 Schema 加载 ============


def load_tool_schemas(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """从策略 YAML 读 ``tool_schemas`` 段（小写工具名 → JSON Schema 子集）。"""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "policies" / "high_risk_tools.yaml"
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("工具 schema 加载失败，跳过 schema 校验: %s", exc)
        return {}
    schemas = raw.get("tool_schemas") or {}
    if not isinstance(schemas, dict):
        return {}
    return {str(k).lower(): v for k, v in schemas.items() if isinstance(v, dict)}


# ============ 提取器 ============


def _coerce_arguments(raw: Any) -> tuple[Any, str]:
    """把原始 arguments 统一成 (解析值, 原文字符串)。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            return raw, raw
    if isinstance(raw, (dict, list)):
        try:
            return raw, json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return raw, str(raw)
    if raw is None:
        return {}, "{}"
    return raw, str(raw)


def extract_tool_calls(payload: Any, *, protocol: str = "auto") -> list[ExtractedToolCall]:
    """从 LLM 响应 / assistant message 中提取工具调用。

    payload 可为：
      - OpenAI assistant message dict（含 ``tool_calls`` 或 ``function_call``）
      - OpenAI 完整响应 dict（含 ``choices[0].message``）
      - Anthropic message dict（含 ``content`` 列表）
      - Anthropic content block 列表
      - 直接的 tool call dict 列表

    protocol:
      - ``"openai"`` / ``"anthropic"``：强制按该协议解析
      - ``"auto"``：依次尝试（OpenAI 优先，再 Anthropic）
    """
    if protocol in (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC):
        return _extract_as(payload, protocol)
    out = _extract_as(payload, PROTOCOL_OPENAI)
    if not out:
        out = _extract_as(payload, PROTOCOL_ANTHROPIC)
    return out


def _extract_as(payload: Any, protocol: str) -> list[ExtractedToolCall]:
    if payload is None:
        return []
    # 完整响应体 → 取 message
    if isinstance(payload, dict):
        if "choices" in payload:
            choices = payload.get("choices") or []
            if choices:
                payload = choices[0].get("message") or {}
            else:
                return []
        elif "message" in payload and isinstance(payload["message"], dict):
            payload = payload["message"]

    if protocol == PROTOCOL_OPENAI:
        return _extract_openai(payload)
    return _extract_anthropic(payload)


def _extract_openai(node: Any) -> list[ExtractedToolCall]:
    calls: list[ExtractedToolCall] = []
    if isinstance(node, list):
        # 直接是 tool_calls 数组
        for item in node:
            calls.extend(_extract_openai(item))
        return calls
    if not isinstance(node, dict):
        return calls

    # 新格式：tool_calls
    for idx, tc in enumerate(node.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "")
        parsed, raw = _coerce_arguments(fn.get("arguments"))
        calls.append(
            ExtractedToolCall(
                call_id=str(tc.get("id") or f"call_{uuid.uuid4().hex[:12]}_{idx}"),
                name=name,
                arguments=parsed,
                raw_arguments=raw,
                protocol=PROTOCOL_OPENAI,
            )
        )

    # 老格式：function_call
    legacy = node.get("function_call")
    if isinstance(legacy, dict) and legacy:
        parsed, raw = _coerce_arguments(legacy.get("arguments"))
        calls.append(
            ExtractedToolCall(
                call_id=f"call_{uuid.uuid4().hex[:12]}_legacy",
                name=str(legacy.get("name") or ""),
                arguments=parsed,
                raw_arguments=raw,
                protocol=PROTOCOL_OPENAI,
            )
        )

    # 直接是单个 tool_call 的形状
    if not calls and "function" in node and isinstance(node["function"], dict):
        fn = node["function"]
        parsed, raw = _coerce_arguments(fn.get("arguments"))
        calls.append(
            ExtractedToolCall(
                call_id=str(node.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                name=str(fn.get("name") or ""),
                arguments=parsed,
                raw_arguments=raw,
                protocol=PROTOCOL_OPENAI,
            )
        )
    return [c for c in calls if c.name]


def _extract_anthropic(node: Any) -> list[ExtractedToolCall]:
    calls: list[ExtractedToolCall] = []
    blocks = node
    if isinstance(node, dict):
        blocks = node.get("content")
        if blocks is None:
            # 单个 tool_use block
            blocks = [node]
    if not isinstance(blocks, list):
        return calls
    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        parsed, raw = _coerce_arguments(block.get("input"))
        calls.append(
            ExtractedToolCall(
                call_id=str(block.get("id") or f"toolu_{uuid.uuid4().hex[:12]}_{idx}"),
                name=str(block.get("name") or ""),
                arguments=parsed,
                raw_arguments=raw,
                protocol=PROTOCOL_ANTHROPIC,
            )
        )
    return [c for c in calls if c.name]


# ============ 守卫 ============


class ToolGuard:
    """对提取出的工具调用逐个做 L4 体检（B6 egress 复用）。

    C3：可注入 ``PolicyRegistry``。注入后 ``policy`` / ``schemas`` 每次读取都取
    最新快照 —— 演练时"改一行 ``high_risk_tools.yaml`` → 新工具立刻被拦"无需重启。
    """

    def __init__(
        self,
        *,
        policy: ToolPolicy | None = None,
        schemas: dict[str, dict[str, Any]] | None = None,
        registry: PolicyRegistry | None = None,
    ) -> None:
        self.registry = registry
        if registry is not None:
            # 走热更新模式：显式传入的 policy/schemas 仅作兜底（registry 未加载时用）
            self._policy = policy
            self._schemas = schemas
        else:
            self._policy = policy if policy is not None else load_tool_policy()
            self._schemas = schemas if schemas is not None else load_tool_schemas()

    # ---- C3：策略动态读 ----

    @property
    def policy(self) -> ToolPolicy:
        if self.registry is not None and self.registry.loaded:
            return self.registry.tool_policy
        if self._policy is None:
            self._policy = load_tool_policy()
        return self._policy

    @policy.setter
    def policy(self, value: ToolPolicy) -> None:
        self._policy = value

    @property
    def schemas(self) -> dict[str, dict[str, Any]]:
        if self.registry is not None and self.registry.loaded:
            return self.registry.tool_schemas
        if self._schemas is None:
            self._schemas = load_tool_schemas()
        return self._schemas

    @schemas.setter
    def schemas(self, value: dict[str, dict[str, Any]]) -> None:
        self._schemas = value

    def guard_call(self, call: ExtractedToolCall) -> GuardedToolCall:
        schema = self.schemas.get(call.name.lower())
        decision = check_tool_call(
            call.name,
            call.arguments,
            policy=self.policy,
            schema=schema,
        )
        return GuardedToolCall(call=call, decision=decision)

    def guard_calls(self, calls: list[ExtractedToolCall]) -> ToolGuardResult:
        guarded = [self.guard_call(c) for c in calls]
        result = ToolGuardResult(guarded=guarded)
        if result.blocked:
            logger.warning(
                "工具调用被拦截 · blocked=%d/%d · reason=%s",
                len(result.blocked),
                len(guarded),
                result.reason[:200],
            )
        return result

    def guard_payload(self, payload: Any, *, protocol: str = "auto") -> ToolGuardResult:
        return self.guard_calls(extract_tool_calls(payload, protocol=protocol))


# ============ 回灌给模型的 tool_result ============


def build_tool_result_error(
    guarded: GuardedToolCall,
    *,
    protocol: str | None = None,
) -> dict[str, Any]:
    """把被拦的工具调用转成 tool_result 错误消息（回灌给模型）。

    这样模型能"知道"自己越界了，进而在下一轮生成解释性回复，
    而不是让调用方直接收到一个不知所云的 400。
    """
    proto = protocol or guarded.call.protocol
    content = json.dumps(
        {
            "error": "blocked_by_agentsentry",
            "tool": guarded.call.name,
            "message": guarded.decision.reason,
            "risk_level": guarded.decision.risk_level.value,
            "hint": (
                "该工具调用被 AgentSoc 安全策略拦截，未执行。"
                "请改用更安全的替代方案，或向用户说明为何需要该操作。"
            ),
        },
        ensure_ascii=False,
    )

    if proto == PROTOCOL_ANTHROPIC:
        return {
            "type": "tool_result",
            "tool_use_id": guarded.call.call_id,
            "content": content,
            "is_error": True,
        }
    return {
        "role": "tool",
        "tool_call_id": guarded.call.call_id,
        "name": guarded.call.name,
        "content": content,
    }


def build_tool_result_ok(
    guarded: GuardedToolCall,
    output: Any,
    *,
    protocol: str | None = None,
) -> dict[str, Any]:
    """把成功执行的工具结果转成 tool_result 消息。"""
    proto = protocol or guarded.call.protocol
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    if proto == PROTOCOL_ANTHROPIC:
        return {
            "type": "tool_result",
            "tool_use_id": guarded.call.call_id,
            "content": content,
            "is_error": False,
        }
    return {
        "role": "tool",
        "tool_call_id": guarded.call.call_id,
        "name": guarded.call.name,
        "content": content,
    }


__all__ = [
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "ExtractedToolCall",
    "GuardedToolCall",
    "ToolGuard",
    "ToolGuardResult",
    "build_tool_result_error",
    "build_tool_result_ok",
    "extract_tool_calls",
    "load_tool_schemas",
]
