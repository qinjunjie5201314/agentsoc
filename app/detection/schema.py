"""M1 检测层 · L3 数据结构定义。

定义：
  - RiskLevel / RiskAction（复用 app.audit.models）
  - Rule（单条 YAML 规则的反序列化结构）
  - RuleHit（规则命中后的产出）
  - DetectionResult（L3 整个检测流水线的产出）
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal

from app.audit.models import RiskAction, RiskLevel, SourceType

# 规则严重程度（语义层）和处置动作的默认映射
DEFAULT_ACTION_FOR_LEVEL = {
    RiskLevel.LOW: RiskAction.ALLOW,
    RiskLevel.MEDIUM: RiskAction.CONFIRM,
    RiskLevel.HIGH: RiskAction.BLOCK,
}


class PatternType(str, enum.Enum):
    """规则匹配类型。"""

    REGEX = "regex"
    KEYWORD_ANY = "keyword_any"  # 任一关键词命中
    KEYWORD_ALL = "keyword_all"  # 所有关键词都命中
    LENGTH = "length"  # 长度阈值（防御上下文窗口攻击）


@dataclass
class Pattern:
    """单条匹配模式。"""

    type: PatternType
    value: Any  # regex: str; keyword_*: list[str]; length: dict
    flags: list[str] = field(default_factory=list)  # regex 可选 ['ignorecase', 'multiline']

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "value": self.value, "flags": self.flags}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        return cls(
            type=PatternType(data["type"]),
            value=data["value"],
            flags=data.get("flags", []),
        )


@dataclass
class Rule:
    """单条检测规则。

    YAML DSL：
        id: ignore_previous
        description: 经典忽略之前指令注入
        severity: high              # low / medium / high
        action: block               # allow / confirm / block（可选，覆盖 severity 默认）
        sources: [user, tool]       # 限制规则适用来源（默认 [user, tool]）
        skip_normalize: false       # 是否跳过 L1 归一化（默认 false）
        patterns:
          - type: regex
            value: '...'
            flags: [ignorecase]
        message: '检测提示'
        tags: [prompt_injection, classic]
    """

    id: str
    severity: RiskLevel
    patterns: list[Pattern]
    description: str = ""
    action: RiskAction | None = None  # 若为 None，使用 severity 默认映射
    sources: list[SourceType] = field(
        default_factory=lambda: [SourceType.USER, SourceType.TOOL]
    )
    skip_normalize: bool = False
    message: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "severity": self.severity.value,
            "action": self.action.value if self.action else None,
            "sources": [s.value for s in self.sources],
            "skip_normalize": self.skip_normalize,
            "patterns": [p.to_dict() for p in self.patterns],
            "message": self.message,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        sources_raw = data.get("sources", ["user", "tool"])
        sources = [SourceType(s) for s in sources_raw] if isinstance(sources_raw[0], str) else sources_raw
        return cls(
            id=data["id"],
            severity=RiskLevel(data["severity"]),
            patterns=[Pattern.from_dict(p) for p in data["patterns"]],
            description=data.get("description", ""),
            action=RiskAction(data["action"]) if data.get("action") else None,
            sources=sources,
            skip_normalize=data.get("skip_normalize", False),
            message=data.get("message", ""),
            tags=data.get("tags", []),
        )

    @property
    def effective_action(self) -> RiskAction:
        """根据 action 显式指定或 severity 严重程度推导最终动作。"""
        return self.action or DEFAULT_ACTION_FOR_LEVEL[self.severity]


@dataclass
class RuleHit:
    """单条规则命中结果。"""

    rule_id: str
    severity: RiskLevel
    action: RiskAction
    matched_pattern_index: int
    matched_text: str  # 命中的具体片段（截断）
    source: SourceType
    message: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "action": self.action.value,
            "matched_pattern_index": self.matched_pattern_index,
            "matched_text": self.matched_text,
            "source": self.source.value,
            "message": self.message,
            "tags": self.tags,
        }


@dataclass
class DetectionResult:
    """L3 检测流水线的总产出。"""

    session_id: str | None
    layer: Literal["L3"] = "L3"
    hits: list[RuleHit] = field(default_factory=list)
    source_segments: list[dict[str, Any]] = field(default_factory=list)  # 喂入检测的段
    elapsed_ms: float = 0.0

    @property
    def has_hits(self) -> bool:
        return bool(self.hits)

    @property
    def max_severity(self) -> RiskLevel:
        """所有命中里的最高严重程度。"""
        if not self.hits:
            return RiskLevel.LOW
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return max(self.hits, key=lambda h: order[h.severity]).severity

    @property
    def final_action(self) -> RiskAction:
        """所有命中里最严格的处置动作。block > confirm > allow。"""
        if not self.hits:
            return RiskAction.ALLOW
        order = {RiskAction.ALLOW: 0, RiskAction.CONFIRM: 1, RiskAction.BLOCK: 2}
        return max(self.hits, key=lambda h: order[h.action]).action

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "session_id": self.session_id,
            "has_hits": self.has_hits,
            "max_severity": self.max_severity.value,
            "final_action": self.final_action.value,
            "hit_count": len(self.hits),
            "hits": [h.to_dict() for h in self.hits],
            "elapsed_ms": self.elapsed_ms,
        }
