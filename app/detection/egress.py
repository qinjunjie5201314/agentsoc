"""M1 检测层 · L4 输出兜底（B6）。

工具调用前的最后关卡。与 L1-L3 不同，L4 关注的是 Agent 即将执行的**动作**本身，
而非输入文本。三件事：

  1. **JSON 校验**：arguments 必须是合法 JSON（字符串需可 json.loads），可选 JSON Schema 校验
  2. **工具白/黑名单**：blocked_tools 直接拦截；allowlist 非空时只放行白名单内工具
  3. **高危语义检测**：工具名 + 序列化后的参数，扫危险正则模式（rm -rf / drop table / eval ...）

设计原则：
  - 归一化后再扫参数文本，防 Base64/URL 编码绕过（复用 B1 normalize）
  - 任一检查失败即 BLOCK，并给出明确 reason（供审计 + 返回给模型）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.audit.models import RiskAction, RiskLevel
from app.detection.normalize import normalize

logger = logging.getLogger(__name__)


# ============ 数据结构 ============


@dataclass
class EgressCheck:
    """单项检查结果。"""

    name: str  # 检查项名（json_valid / schema_valid / blocklist / allowlist / dangerous_args）
    passed: bool
    detail: str = ""


@dataclass
class EgressDecision:
    """L4 兜底决策。"""

    tool_name: str
    arguments: Any  # 解析后的参数（dict / list / str）
    action: RiskAction
    risk_level: RiskLevel
    checks: list[EgressCheck] = field(default_factory=list)
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.action is RiskAction.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "action": self.action.value,
            "risk_level": self.risk_level.value,
            "passed": self.passed,
            "reason": self.reason,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


# ============ 策略加载 ============


@dataclass
class ToolPolicy:
    """工具兜底策略（从 high_risk_tools.yaml 加载）。"""

    blocked_tools: list[str] = field(default_factory=list)
    allowlist: list[str] = field(default_factory=list)
    dangerous_patterns: list[tuple[str, str, str]] = field(default_factory=list)  # (name, regex, msg)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolPolicy:
        patterns: list[tuple[str, str, str]] = []
        for p in data.get("dangerous_patterns", []):
            patterns.append((p["name"], p["pattern"], p.get("message", p["name"])))
        return cls(
            blocked_tools=[str(t) for t in data.get("blocked_tools", [])],
            allowlist=[str(t) for t in data.get("allowlist", [])],
            dangerous_patterns=patterns,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> ToolPolicy:
        p = Path(path)
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError(f"工具策略文件 {path} 顶层必须是对象，实际为 {type(raw).__name__}")
        return cls.from_dict(raw)


def load_tool_policy(path: str | Path | None = None) -> ToolPolicy:
    """加载工具策略。默认取项目根 policies/high_risk_tools.yaml。"""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "policies" / "high_risk_tools.yaml"
    return ToolPolicy.from_yaml(path)


# ============ 极简 JSON Schema 子集校验 ============


def _check_schema(instance: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    """递归校验，支持 type/required/properties/items/enum。"""
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            errors.append(f"{path or '$'} 应为对象")
            return
        for k in schema.get("required", []):
            if k not in instance:
                errors.append(f"{path or '$'} 缺少必填字段 {k!r}")
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                _check_schema(instance[k], sub, f"{path}.{k}" if path else k, errors)
    elif t == "array":
        if not isinstance(instance, list):
            errors.append(f"{path or '$'} 应为数组")
            return
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                _check_schema(item, items, f"{path}[{i}]", errors)
    elif t == "string":
        if not isinstance(instance, str):
            errors.append(f"{path or '$'} 应为字符串")
    elif t == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            errors.append(f"{path or '$'} 应为数字")
    elif t == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            errors.append(f"{path or '$'} 应为整数")
    elif t == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path or '$'} 应为布尔")

    enum_vals = schema.get("enum")
    if enum_vals is not None and instance not in enum_vals:
        errors.append(f"{path or '$'} 不在允许枚举中: {enum_vals}")


def _validate_against_schema(instance: Any, schema: dict[str, Any] | None) -> list[str]:
    if not schema:
        return []
    errors: list[str] = []
    _check_schema(instance, schema, "", errors)
    return errors


def _flatten_text(value: Any) -> str:
    """把参数序列化成可扫描的纯文本。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


# ============ 主入口 ============


def check_tool_call(
    tool_name: str,
    arguments: Any,
    *,
    policy: ToolPolicy | None = None,
    schema: dict[str, Any] | None = None,
) -> EgressDecision:
    """对一次工具调用做 L4 兜底检查。

    Args:
        tool_name: 工具名（如 "bash" / "drop_database" / "search"）。
        arguments: 工具参数，可为 dict / list / JSON 字符串。
        policy: 工具策略（None 时加载默认 YAML）。
        schema: 可选的 JSON Schema（dict），对解析后的参数做结构校验。

    Returns:
        EgressDecision，任一检查失败即 BLOCK。
    """
    policy = policy or load_tool_policy()
    checks: list[EgressCheck] = []

    # ---- 1) 参数 JSON 合法性 ----
    parsed: Any = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            checks.append(EgressCheck("json_valid", True, f"参数为合法 JSON（{type(parsed).__name__}）"))
        except json.JSONDecodeError as exc:
            checks.append(EgressCheck("json_valid", False, f"参数不是合法 JSON: {exc}"))
    elif isinstance(arguments, (dict, list)):
        checks.append(EgressCheck("json_valid", True, f"参数类型 {type(parsed).__name__}"))
    else:
        checks.append(EgressCheck("json_valid", False, f"参数类型非法: {type(parsed).__name__}"))

    # ---- 1b) 可选 JSON Schema 校验 ----
    if isinstance(parsed, (dict, list)):
        schema_errors = _validate_against_schema(parsed, schema)
        if schema_errors:
            checks.append(EgressCheck("schema_valid", False, "; ".join(schema_errors[:3])))
        else:
            checks.append(EgressCheck("schema_valid", True, "schema 校验通过"))

    # ---- 2) 工具白/黑名单 ----
    name_lower = (tool_name or "").lower()
    allow_set = {t.lower() for t in policy.allowlist}
    block_set = {t.lower() for t in policy.blocked_tools}

    if allow_set and name_lower not in allow_set:
        checks.append(EgressCheck("allowlist", False, f"工具 {tool_name!r} 不在白名单"))
    else:
        checks.append(EgressCheck("allowlist", True, "白名单通过（或未启用）"))

    if name_lower in block_set:
        checks.append(EgressCheck("blocklist", False, f"工具 {tool_name!r} 在黑名单"))
    else:
        checks.append(EgressCheck("blocklist", True, f"工具 {tool_name!r} 不在黑名单"))

    # ---- 3) 高危语义检测（工具名 + 参数，均先归一化） ----
    args_text = _flatten_text(parsed)
    args_norm = normalize(args_text) or args_text
    name_norm = normalize(name_lower) or name_lower

    hits: list[str] = []
    for name, pattern, message in policy.dangerous_patterns:
        if re.search(pattern, args_norm, re.IGNORECASE):
            hits.append(f"{name}({message})")
        elif re.search(pattern, name_norm, re.IGNORECASE):
            hits.append(f"tool_name:{name}({message})")

    if hits:
        checks.append(EgressCheck("dangerous_args", False, "命中高危模式: " + ", ".join(hits)))
    else:
        checks.append(EgressCheck("dangerous_args", True, "工具名与参数无高危模式"))

    # ---- 汇总 ----
    failed = [c for c in checks if not c.passed]
    if failed:
        action = RiskAction.BLOCK
        risk_level = RiskLevel.HIGH
        reason = "; ".join(f"{c.name}: {c.detail}" for c in failed)
    else:
        action = RiskAction.ALLOW
        risk_level = RiskLevel.LOW
        reason = ""

    return EgressDecision(
        tool_name=tool_name,
        arguments=parsed,
        action=action,
        risk_level=risk_level,
        checks=checks,
        reason=reason,
    )
