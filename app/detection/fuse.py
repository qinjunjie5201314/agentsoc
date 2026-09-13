"""M1 检测层 · L3 决策融合（B5）。

职责：把 B3 规则命中 + B4 判别模型分数融合成统一输出：
  - ``risk_level`` ∈ {low, medium, high}
  - ``final_action`` ∈ {allow, confirm, block}

决策矩阵（保守取向，宁可误报不漏报）——取规则等级与模型等级的较高者：

    +-------------------+-------------------+-----------------+
    | 规则等级          | 模型等级          | 融合后           |
    +===================+===================+=================+
    | 任一方 HIGH       | （任意）          | HIGH   → block   |
    | 任一方 MEDIUM     | 另一方 ≤ MEDIUM   | MEDIUM → confirm |
    | 双方 LOW / 无命中 | 双方 LOW / 无分数 | LOW    → allow   |
    +-------------------+-------------------+-----------------+

这是安全产品的默认取向：漏报代价远高于误报代价。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.audit.models import RiskAction, RiskLevel
from app.detection.classifier import (
    INJECTION_THRESHOLD_HIGH,
    INJECTION_THRESHOLD_LOW,
    ClassifierScore,
)
from app.detection.schema import DEFAULT_ACTION_FOR_LEVEL, DetectionResult, RuleHit

_LEVEL_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def score_to_level(
    score: float,
    *,
    threshold_high: float = INJECTION_THRESHOLD_HIGH,
    threshold_low: float = INJECTION_THRESHOLD_LOW,
) -> RiskLevel:
    """把连续判别分数映射到风险等级。

    阈值默认沿用 classifier 的常量（high=0.85 / low=0.5），可注入以适配不同 provider。
    """
    if score >= threshold_high:
        return RiskLevel.HIGH
    if score >= threshold_low:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


@dataclass
class FusedDecision:
    """融合后的最终决策。"""

    rule_level: RiskLevel
    classifier_level: RiskLevel
    risk_level: RiskLevel
    final_action: RiskAction
    max_score: float
    rule_hits: list[RuleHit] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level.value,
            "final_action": self.final_action.value,
            "rule_level": self.rule_level.value,
            "classifier_level": self.classifier_level.value,
            "max_score": round(self.max_score, 4),
            "rule_hit_count": len(self.rule_hits),
            "reasons": self.reasons,
        }


def fuse(
    detection: DetectionResult,
    classifier_scores: list[ClassifierScore] | None = None,
    *,
    threshold_high: float = INJECTION_THRESHOLD_HIGH,
    threshold_low: float = INJECTION_THRESHOLD_LOW,
) -> FusedDecision:
    """融合规则命中与判别分数，输出统一决策。

    Args:
        detection: B3 规则引擎产出（含命中列表）。
        classifier_scores: B4 判别模型对每个段落的评分（可为空/None）。
        threshold_high / threshold_low: 分数分级阈值（默认沿用 classifier 常量）。

    Returns:
        FusedDecision，含最终 risk_level 与 final_action。
    """
    scores = classifier_scores or []
    max_score = max((s.score for s in scores), default=0.0)

    rule_level = detection.max_severity
    classifier_level = score_to_level(
        max_score,
        threshold_high=threshold_high,
        threshold_low=threshold_low,
    )

    # 取较高者（union）
    risk_level = max(rule_level, classifier_level, key=_LEVEL_ORDER.__getitem__)
    final_action = DEFAULT_ACTION_FOR_LEVEL[risk_level]

    # 生成可读理由，便于审计与 CLI 展示
    reasons: list[str] = []
    if detection.has_hits:
        reasons.append(f"规则命中 {len(detection.hits)} 条（最高 {rule_level.value}）")
    else:
        reasons.append("规则无命中")
    if scores:
        reasons.append(f"判别模型最高分 {max_score:.3f} → {classifier_level.value}")
    else:
        reasons.append("判别模型未启用")

    return FusedDecision(
        rule_level=rule_level,
        classifier_level=classifier_level,
        risk_level=risk_level,
        final_action=final_action,
        max_score=max_score,
        rule_hits=detection.hits,
        reasons=reasons,
    )
