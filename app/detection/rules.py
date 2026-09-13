"""M1 检测层 · L3 规则引擎。

职责：
  - 从 YAML 加载规则
  - 对 TaggedContent 列表执行模式匹配
  - 按 sources 字段做来源过滤（只扫描 user/tool）
  - 与 L1 归一化无缝衔接（默认先归一再匹配，规则显式 skip_normalize 可跳过）
  - 输出 DetectionResult（含命中/动作/严重度）

不做：
  - 不做 L4 工具调用兜底（那是 egress.py）
  - 不做模型判别（B4 才做）
  - 不做动作执行（决策落库 / 调用拦截是 C1/C2 的事）
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from app.audit.models import SourceType
from app.detection.isolate import TaggedContent
from app.detection.normalize import normalize
from app.detection.schema import DetectionResult, Pattern, PatternType, Rule, RuleHit

logger = logging.getLogger(__name__)


# ============ YAML 加载 ============

def load_rules_from_yaml(path: str | Path) -> list[Rule]:
    """从 YAML 文件加载规则列表。

    文件格式：
        rules:
          - id: rule_xxx
            severity: high
            ...
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or "rules" not in raw:
        raise ValueError(f"规则文件 {path} 必须包含 'rules' 顶层字段")

    rules: list[Rule] = []
    for item in raw["rules"]:
        try:
            rules.append(Rule.from_dict(item))
        except Exception as exc:
            logger.error("规则 %s 解析失败: %s", item.get("id"), exc)
            raise
    return rules


def load_rules_from_dict(data: dict) -> list[Rule]:
    """从内存 dict 加载规则（便于测试 / 热更新）。"""
    if "rules" not in data:
        raise ValueError("必须包含 'rules' 顶层字段")
    return [Rule.from_dict(item) for item in data["rules"]]


# ============ 模式匹配 ============

def _regex_flags(flags: Sequence[str]) -> int:
    out = 0
    if "ignorecase" in flags:
        out |= re.IGNORECASE
    if "multiline" in flags:
        out |= re.MULTILINE
    if "dotall" in flags:
        out |= re.DOTALL
    return out


def match_pattern(pattern: Pattern, text: str) -> tuple[bool, str]:
    """单条模式匹配。返回 (是否命中, 命中的文本片段)。

    不同类型的行为：
      - regex：re.search 返回首个 match，取 span
      - keyword_any：任一关键词出现即命中
      - keyword_all：所有关键词都出现才命中
      - length：仅判断布尔；matched_text 返回长度相关描述
    """
    if pattern.type == PatternType.REGEX:
        flags = _regex_flags(pattern.flags)
        m = re.search(pattern.value, text, flags=flags)
        if m:
            return True, text[m.start() : m.end()][:200]
        return False, ""

    if pattern.type == PatternType.KEYWORD_ANY:
        lowered = text.lower()
        for kw in pattern.value:
            if kw.lower() in lowered:
                idx = lowered.index(kw.lower())
                start = max(0, idx - 20)
                end = min(len(text), idx + len(kw) + 20)
                return True, text[start:end][:200]
        return False, ""

    if pattern.type == PatternType.KEYWORD_ALL:
        lowered = text.lower()
        missing = [kw for kw in pattern.value if kw.lower() not in lowered]
        if not missing:
            return True, ", ".join(pattern.value)[:200]
        return False, ""

    if pattern.type == PatternType.LENGTH:
        cfg = pattern.value or {}
        threshold = int(cfg.get("max", 0))
        if threshold and len(text) > threshold:
            return True, f"length={len(text)}>max={threshold}"
        return False, ""

    raise ValueError(f"未知 pattern type: {pattern.type}")


def match_rule(rule: Rule, text: str, source: SourceType) -> RuleHit | None:
    """对单段文本按 rule 匹配，返回首个命中或 None。"""
    if source not in rule.sources:
        return None

    # 准备被匹配的文本
    target = text if rule.skip_normalize else normalize(text)
    if target is None:
        target = ""

    for idx, pat in enumerate(rule.patterns):
        hit, matched_text = match_pattern(pat, target)
        if hit:
            return RuleHit(
                rule_id=rule.id,
                severity=rule.severity,
                action=rule.effective_action,
                matched_pattern_index=idx,
                matched_text=matched_text,
                source=source,
                message=rule.message or rule.description,
                tags=list(rule.tags),
            )

    # 即使没命中 pattern，全规则无 hits
    return None


# ============ 扫描 ============

def scan(
    rules: Sequence[Rule],
    tagged_contents: Iterable[TaggedContent],
    *,
    session_id: str | None = None,
) -> DetectionResult:
    """用一组规则扫描 TaggedContent 列表。

    Args:
        rules: 已加载的规则
        tagged_contents: B2 输出
        session_id: 可选 session_id，用于审计

    Returns:
        DetectionResult 含所有命中
    """
    started = time.perf_counter()
    result = DetectionResult(session_id=session_id)
    sources_seen: set[SourceType] = set()

    for seg in tagged_contents:
        sources_seen.add(seg.source)
        # 记录喂入检测的源信息
        result.source_segments.append(
            {
                "source": seg.source.value,
                "role": seg.role,
                "length": len(seg.content),
                "preview": seg.content[:60],
            }
        )
        for rule in rules:
            hit = match_rule(rule, seg.content, seg.source)
            if hit:
                result.hits.append(hit)

    result.elapsed_ms = (time.perf_counter() - started) * 1000
    logger.debug(
        "L3 scan 完成 · rules=%d · segments=%d · sources=%s · hits=%d · %.2fms",
        len(rules),
        len(result.source_segments),
        sorted(s.value for s in sources_seen),
        len(result.hits),
        result.elapsed_ms,
    )
    return result


# ============ 便捷构建 ============

def quick_scan(
    rules: Sequence[Rule],
    text: str,
    *,
    source: SourceType = SourceType.USER,
    session_id: str | None = None,
) -> DetectionResult:
    """单段文本快速扫描，便于测试 / CLI 调用。"""
    return scan(rules, [TaggedContent(content=text, source=source)], session_id=session_id)
