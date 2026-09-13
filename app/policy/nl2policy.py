"""C4 自然语言转策略 - 主引擎。

目标
----
用户写一句自然语言（例如 "禁止用 bash 删除 /etc 下的文件"），
把这条 NL 描述**确定性 + 透明**地翻译成一段 YAML（policy 片段），
并送进 :func:`PolicyRegistry.validate_yaml` 做预检。

设计取舍
--------
1. **不做 NLU 模型**：上线初期只用规则 + 关键词模板，依赖简单可靠；
   复杂句式识别率低时，会显式返回 ``Intent.UNCERTAIN``，引导用户去手动 YAML。
2. **永远返回结构化报告**：生成的 YAML、抽取到的实体、识别的意图、可能的歧义，
   调用方可据此选"接受 / 改写 / 丢弃"。
3. **不直接落盘**：默认 ``dry_run=True`` 只产出草稿；显式 ``dry_run=False`` 才写策略目录。
4. **不依赖网络**：完全离线、纯 Python。

意图分类（v1）
--------------
- ``BLOCK_TOOL``              禁止调用某个工具
- ``BLOCK_TOOL_ARG_PATTERN``  禁止某工具的某类参数（含具体命令/路径/关键词）
- ``ADD_RULE``                新增一条 L3 规则（关键词/regex/路径）
- ``UNCERTAIN``               拿不准，提示用户改写或人工

每种意图对应一种 YAML 草稿（见 ``_DRAFTERS``）。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

import yaml

from app.detection.schema import PatternType
from app.policy.intents import (
    detect_dangerous_cmd,
    extract_paths,
    extract_pattern_tokens,
    extract_quoted,
    extract_tools,
    is_block_intent,
    looks_like_add_rule,
    normalize_pattern_type,
)
from app.policy.registry import PolicyRegistry

# =========================== YAML ↔ schema 适配 ===========================


def _to_schema_pattern(p: dict[str, Any]) -> dict[str, Any]:
    """把 nl2policy 内部 pattern 形态适配到 :class:`PatternType` schema。

    - keyword       → keyword_any + value 转 list
    - regex         → regex 不变
    - path          → regex（用 ``re.escape`` 处理原字面，最保守）
    - 其它 unknown  → keyword_any 兜底
    """
    import re as _re

    ptype = p.get("type", "keyword")
    value = p.get("value", "")

    if ptype == "regex":
        return {"type": PatternType.REGEX.value, "value": str(value), "flags": p.get("flags", [])}
    if ptype == "path":
        # 路径直接当 regex，最小化匹配代价
        return {
            "type": PatternType.REGEX.value,
            "value": _re.escape(str(value)),
            "flags": [],
        }
    # 默认/keyword：把单个 str 包成 list，转 keyword_any
    sval = str(value)
    return {"type": PatternType.KEYWORD_ANY.value, "value": [sval] if sval else [""], "flags": []}

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    BLOCK_TOOL = "block_tool"
    BLOCK_TOOL_ARG_PATTERN = "block_tool_arg_pattern"
    ADD_RULE = "add_rule"
    UNCERTAIN = "uncertain"


# 一个 NL 进来能被识别的最低置信度（导出方便 UI 调阈值）
MIN_CONFIDENCE: Final[float] = 0.55


@dataclass
class IntentClassification:
    """NL → intent 的识别结果。"""

    intent: Intent
    confidence: float              # 0..1
    reason: str = ""               # 一句话解释（中文）
    entities: dict[str, list[str]] = field(default_factory=dict)
    # 常见 entities: tools / paths / patterns / quoted / dangerous_cmd

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "entities": self.entities,
        }


@dataclass
class DraftPolicy:
    """自然语言翻译出的 YAML 草稿 + 校验报告。"""

    classification: IntentClassification
    yaml_text: str
    structure: dict[str, Any]               # 解析后的 dict（结构化预览）
    summary: str                           # 一句话描述草稿做什么
    validation: dict[str, Any]             # 注册表 ``validate_yaml`` 结果
    filename_suggestion: str               # 默认 'nl_<时间戳>.yaml'
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.to_dict(),
            "summary": self.summary,
            "yaml": self.yaml_text,
            "structure": self.structure,
            "validation": self.validation,
            "filename_suggestion": self.filename_suggestion,
            "dry_run": self.dry_run,
        }


# =========================== 草稿生成器 ===========================


def _rule_id_from_text(text: str) -> str:
    """从自然语言派生规则 id（rule_<uuid前8位> + 基于文本的 slug）。"""
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", text.lower())[:30].strip("_")
    slug = re.sub(r"_+", "_", slug) or "misc"
    return f"nl_{slug}_{uuid.uuid4().hex[:6]}"


def _draft_block_tool(tools: list[str], text: str) -> dict[str, Any]:
    """意图：禁用工具（整工具黑名单）。"""
    return {
        "blocked_tools": list(dict.fromkeys(tools)),
        "_summary": f"禁止调用工具：{', '.join(tools)}",
    }


def _draft_block_tool_arg_pattern(
    tools: list[str], patterns: list[str], paths: list[str], quoted: list[str], dangerous_cmd: str | None,
    severity: str,
) -> dict[str, Any]:
    """意图：禁某工具的某类参数（危险命令 / 路径 / 关键词）。

    输出符合 ``ToolPolicy.from_dict`` 的 ``dangerous_patterns`` schema：
    每条 ``{name, pattern, message}``（pattern 为正则字符串）。
    """
    dangerous_patterns: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    import re as _re

    def _emit(tool: str, raw: str, kind: str, severity_: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        key = (tool, raw)
        if key in seen:
            return
        seen.add(key)
        # 把"自然语言类型"映射到正经 regex
        if kind == "regex":
            value = raw
            msg = f"危险命令 {raw!r} 不应通过 {tool} 执行"
        elif kind == "path":
            value = _re.escape(raw)
            msg = f"{tool} 不应访问路径 {raw!r}"
        else:
            value = _re.escape(raw)
            msg = f"{tool} 的参数 {raw!r} 命中阻止规则"
        dangerous_patterns.append({
            "name": f"nl_{tool}_{abs(hash(raw)) % 0xFFFF:04x}",
            "pattern": value,
            "message": msg,
            "_tool": tool,
            "_severity": severity_,
            "_kind": kind,
        })

    for tool in tools:
        # 1) 危险命令（regex）—— 优先级最高
        if dangerous_cmd:
            _emit(tool, dangerous_cmd, "regex", severity)
        # 2) 抽出路径 → path 类型（regex 实现）
        for p in paths:
            _emit(tool, p, "path", severity)
        # 3) 引号内容 / 其他关键词 → keyword（regex 转义实现）
        for q in quoted + patterns:
            if not q or q == dangerous_cmd:
                continue
            _emit(tool, q, "keyword", severity)

    return {
        "dangerous_patterns": dangerous_patterns,
        "_summary": f"禁止 {tools} 执行命中 {len(dangerous_patterns)} 条模式的调用",
    }


def _draft_add_rule(patterns: list[str], quoted: list[str], text: str) -> dict[str, Any]:
    """意图：新增 L3 规则（关键词/regex/路径）。"""
    payload = quoted + patterns
    if not payload:
        payload = [text.strip()[:40]]
    rid = _rule_id_from_text(text)
    patterns_list = []
    seen: set[str] = set()
    for p in payload:
        if not p or p in seen:
            continue
        seen.add(p)
        patterns_list.append(_to_schema_pattern({
            "type": normalize_pattern_type(p),
            "value": p,
        }))
    return {
        "rules": [{
            "id": rid,
            "description": f"N→Y 自动规则（来自：{text[:40]}）",
            "severity": "high",
            "patterns": patterns_list,
        }],
        "_summary": f"新增 1 条规则，{len(patterns_list)} 个模式（关键词/regex/路径）",
    }


_DRAFTERS = {
    Intent.BLOCK_TOOL: lambda cls, text: _draft_block_tool(
        tools=cls.entities.get("tools", []),
        text=text,
    ),
    Intent.BLOCK_TOOL_ARG_PATTERN: lambda cls, text: _draft_block_tool_arg_pattern(
        tools=cls.entities.get("tools", []),
        patterns=cls.entities.get("patterns", []),
        paths=cls.entities.get("paths", []),
        quoted=cls.entities.get("quoted", []),
        dangerous_cmd=cls.entities.get("dangerous_cmd", [None])[0] if cls.entities.get("dangerous_cmd") else None,
        severity="high",
    ),
    Intent.ADD_RULE: lambda cls, text: _draft_add_rule(
        patterns=cls.entities.get("patterns", []),
        quoted=cls.entities.get("quoted", []),
        text=text,
    ),
}


# =========================== 分类器 ===========================


def _classify(text: str) -> IntentClassification:
    """规则化 NL → intent + 抽取 entity + 置信度。"""
    blocked = is_block_intent(text)
    is_rule = looks_like_add_rule(text)
    tools = extract_tools(text)
    paths = extract_paths(text)
    quoted = extract_quoted(text)
    dangerous = detect_dangerous_cmd(text)
    tokens = extract_pattern_tokens(text)

    # 集中"潜在 pattern"
    pattern_candidates = list(dict.fromkeys(quoted + tokens))

    # 情况 1：明确 ADD_RULE（用户说"加规则/拦截 prompt"等）+ 有模式
    if is_rule and pattern_candidates:
        return IntentClassification(
            intent=Intent.ADD_RULE,
            confidence=0.85,
            reason="用户表达了「加规则」意图，并提供了具体模式（引号/路径/token）",
            entities={"quoted": quoted, "paths": paths, "patterns": pattern_candidates},
        )

    # 情况 2：BLOCK + 工具有具体命令/路径/参数
    if blocked and tools and (dangerous or paths or quoted):
        return IntentClassification(
            intent=Intent.BLOCK_TOOL_ARG_PATTERN,
            confidence=0.9,
            reason=f"对工具 {tools} 的特定参数（{'危险命令' if dangerous else '路径/关键词'}）做了阻止",
            entities={
                "tools": tools,
                "paths": paths,
                "quoted": quoted,
                "dangerous_cmd": [dangerous] if dangerous else [],
            },
        )

    # 情况 3：BLOCK + 单纯说"禁用工具"
    if blocked and tools:
        return IntentClassification(
            intent=Intent.BLOCK_TOOL,
            confidence=0.8,
            reason="用户表达了「禁止调用整个工具」的意图",
            entities={"tools": tools},
        )

    # 情况 4：仅有"添加规则"但没模式 → uncertain
    if is_rule:
        return IntentClassification(
            intent=Intent.UNCERTAIN,
            confidence=0.5,
            reason="理解到「加规则」但没看到具体模式；请用引号或示例补全",
            entities={"quoted": quoted, "paths": paths, "patterns": pattern_candidates},
        )

    # 情况 5：啥都识别不出
    return IntentClassification(
        intent=Intent.UNCERTAIN,
        confidence=0.1,
        reason="无法识别意图；M1 仅支持「禁止…」「禁用…」「加规则：…」「block…」类描述",
        entities={
            "tools": tools, "paths": paths, "quoted": quoted,
            "patterns": pattern_candidates,
            "dangerous_cmd": [dangerous] if dangerous else [],
        },
    )


# =========================== 主入口 ===========================


def _validate(draft: dict[str, Any], registry: PolicyRegistry | None) -> dict[str, Any]:
    """把生成的 dict 去掉内部字段（下划线前缀）+ 把 dangerous_patterns 收缩为 schema 形态，再喂给 registry 预检。"""
    cleaned = _scrub_internal(draft)
    if not cleaned:
        return {"ok": False, "errors": ["草稿为空"], "warnings": []}
    yaml_text = yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False)
    if registry is not None:
        try:
            return registry.validate_yaml(yaml_text)
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            return {"ok": False, "errors": [f"validate 异常: {exc}"], "warnings": [], "roles": []}
    return {"ok": True, "errors": [], "warnings": [], "roles": []}


def _scrub_internal(obj: Any) -> Any:
    """递归去掉 ``_foo`` 内部字段。"""
    if isinstance(obj, dict):
        return {k: _scrub_internal(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_scrub_internal(v) for v in obj]
    return obj


def nl_to_draft(
    text: str,
    *,
    registry: PolicyRegistry | None = None,
    severity: str = "high",
) -> DraftPolicy:
    """NL → DraftPolicy。

    主流程：抽取 → 分类 → 生成草稿 → 预检。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("自然语言不能为空")

    cls = _classify(text)
    if cls.intent is Intent.UNCERTAIN:
        # 不直接拒绝，给出一份"空验证"草稿方便 UI 提示
        return DraftPolicy(
            classification=cls,
            yaml_text="",
            structure={},
            summary=cls.reason,
            validation={"ok": False, "errors": ["NL 不可解析"], "warnings": [], "roles": []},
            filename_suggestion=f"nl_{int(time.time())}_{uuid.uuid4().hex[:4]}.yaml",
        )

    builder = _DRAFTERS.get(cls.intent)
    if builder is None:  # pragma: no cover - 防御
        raise RuntimeError(f"未注册的意图: {cls.intent}")
    draft = builder(cls, text)
    # 显式 severity（仅 BLOCK_TOOL_ARG_PATTERN 用到）
    for d in draft.get("dangerous_patterns", []) or []:
        d.setdefault("severity", severity)

    # 序列化预检（去掉内部字段，确保 yaml 与 schema 对齐）
    validation = _validate(draft, registry)
    summary = draft.pop("_summary", "")
    cleaned = _scrub_internal(draft)
    yaml_text = yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False)

    return DraftPolicy(
        classification=cls,
        yaml_text=yaml_text,
        structure=cleaned,
        summary=summary,
        validation=validation,
        filename_suggestion=_suggest_filename(),
    )


def _suggest_filename() -> str:
    return f"nl_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.yaml"


# =========================== 演示 / 测试样例 ===========================


EXAMPLES: Final[list[dict[str, str]]] = [
    {
        "text": "禁止调用 get_weather 工具",
        "hint": "整工具黑名单（最简单）",
    },
    {
        "text": "禁用 bash",
        "hint": "整工具黑名单（中文别名）",
    },
    {
        "text": "禁止用 bash 删除 /etc 下文件",
        "hint": "禁某工具访问某类路径",
    },
    {
        "text": '拦截 prompt 中出现 "rm -rf /" 的请求',
        "hint": "L3 关键词规则 + 引号内容抽取",
    },
    {
        "text": "block bash if it runs rm -rf",
        "hint": "英文：禁止某工具 + 危险命令",
    },
    {
        "text": "添加规则：检测包含 PROJECT_X_CODENAME 的 prompt",
        "hint": "L3 关键词规则 / 英文 token",
    },
    {
        "text": "禁止 send_email 发往 https://attacker.example.com",
        "hint": "禁工具 + URL（regex）",
    },
    {
        "text": "禁止 access ~/.ssh/ 路径",
        "hint": "禁路径访问",
    },
]


__all__ = [
    "EXAMPLES",
    "MIN_CONFIDENCE",
    "DraftPolicy",
    "Intent",
    "IntentClassification",
    "nl_to_draft",
]
