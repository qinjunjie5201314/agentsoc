"""L1 -> L2 -> L3 (B3 + B4) detection pipeline.

DetectionPipeline now aggregates two L3 sub-layers:
  - B3 rule engine (deterministic)
  - B4 classifier (heuristic / HF / remote)
Each produces independent scores that B5 (decision fusion) will later merge.

For M1, both run in parallel and their raw outputs are returned separately,
so the demo can show "rule said X, classifier said Y, M5 will fuse them".
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.audit.models import RiskAction, RiskLevel, SourceType
from app.detection.classifier import BaseClassifier, ClassifierScore, MockClassifier
from app.detection.fuse import FusedDecision, fuse
from app.detection.isolate import TaggedContent, tag_messages
from app.detection.rules import load_rules_from_yaml, scan
from app.detection.schema import DetectionResult, Rule

if TYPE_CHECKING:  # pragma: no cover
    from app.policy.registry import PolicyRegistry

logger = logging.getLogger(__name__)


@dataclass
class EnrichedPipelineResult:
    """完整 pipeline 产出：规则 + 判别模型 + 派生聚合。"""

    detection: DetectionResult
    classifier_scores: list[ClassifierScore] = field(default_factory=list)
    classifier_provider: str = "none"
    fused: FusedDecision | None = None

    # C3：本次检测所用的策略版本（事后可追溯"这条拦截是哪版策略做出的"）
    policy_version: str | None = None
    policy_revision: int | None = None

    # ---- 透传到 DetectionResult 的便捷属性 ----
    @property
    def session_id(self) -> str | None:
        return self.detection.session_id

    @property
    def has_hits(self) -> bool:
        return self.detection.has_hits

    @property
    def hits(self):
        return self.detection.hits

    @property
    def max_score(self) -> float:
        """所有判别器评分的最大值。"""
        if not self.classifier_scores:
            return 0.0
        return max(s.score for s in self.classifier_scores)

    @property
    def max_severity(self) -> RiskLevel:
        return self.detection.max_severity

    @property
    def risk_level(self) -> RiskLevel:
        """B5 融合后的风险等级（未融合时退回规则等级）。"""
        if self.fused is not None:
            return self.fused.risk_level
        return self.detection.max_severity

    @property
    def final_action(self) -> RiskAction:
        """B5 融合后的最终动作（未融合时退回规则动作）。"""
        if self.fused is not None:
            return self.fused.final_action
        return self.detection.final_action

    @property
    def source_segments(self):
        return self.detection.source_segments

    @property
    def elapsed_ms(self) -> float:
        return self.detection.elapsed_ms

    def to_dict(self) -> dict[str, Any]:
        d = self.detection.to_dict()
        d["classifier"] = {
            "provider": self.classifier_provider,
            "max_score": round(self.max_score, 4),
            "scores": [s.to_dict() for s in self.classifier_scores],
        }
        if self.fused is not None:
            d["fused"] = self.fused.to_dict()
            # 用融合后的结果覆盖规则引擎的单一动作/等级
            d["risk_level"] = self.fused.risk_level.value
            d["final_action"] = self.fused.final_action.value
        if self.policy_version is not None:
            d["policy"] = {
                "version": self.policy_version,
                "revision": self.policy_revision,
            }
        return d


class DetectionPipeline:
    """L1 + L2 + L3 (规则 + 判别) 端到端。

    C3：可注入 ``PolicyRegistry``。注入后 ``rules`` 每次读取都取最新快照，
    因此策略热更新**无需重建 pipeline**，正在跑的请求下一次检测即生效。
    """

    def __init__(
        self,
        rules: Sequence[Rule],
        *,
        classifier: BaseClassifier | None = None,
        layer: str = "L1+L2+L3",
        registry: PolicyRegistry | None = None,
    ) -> None:
        self._rules = list(rules)
        # 默认挂 MockClassifier，让即使不显式启用判别的 pipeline 也能跑
        self.classifier: BaseClassifier = classifier or MockClassifier()
        self.layer = layer
        # C3：策略配置中心（可选）。给了它，rules 就是"活的"
        self.registry = registry

    # ---- C3：rules 走 registry 动态读 ----

    @property
    def rules(self) -> list[Rule]:
        if self.registry is not None:
            return self.registry.rules
        return self._rules

    @rules.setter
    def rules(self, value: Sequence[Rule]) -> None:
        self._rules = list(value)

    @property
    def policy_version(self) -> str | None:
        return self.registry.version if self.registry is not None else None

    @property
    def policy_revision(self) -> int | None:
        return self.registry.revision if self.registry is not None else None

    @classmethod
    def from_yaml(
        cls,
        path: str,
        *,
        classifier: BaseClassifier | None = None,
    ) -> DetectionPipeline:
        rules = load_rules_from_yaml(path)
        return cls(rules, classifier=classifier)

    def run(
        self,
        messages_or_tagged: Sequence[dict[str, Any]] | Sequence[TaggedContent],
        *,
        session_id: str | None = None,
    ) -> EnrichedPipelineResult:
        """执行一次完整检测。

        支持两种输入：
          - list[dict]（OpenAI 风格） → 内部打 tag（B2）
          - list[TaggedContent]（已打过 tag） → 直接传给检测层
        """
        if messages_or_tagged and isinstance(messages_or_tagged[0], dict):
            tagged = tag_messages(messages_or_tagged)  # type: ignore[arg-type]
        else:
            tagged = list(messages_or_tagged)  # type: ignore[arg-type]

        # C3：每次请求都从 registry 取当前规则（热更新即时生效）
        active_rules = self.rules
        # B3 规则引擎 —— L1 normalize 在内部完成
        detection = scan(active_rules, tagged, session_id=session_id)
        # B4 判别模型
        scores = self.classifier.score_segments(tagged)
        # B5 决策融合
        fused = fuse(detection, scores)

        return EnrichedPipelineResult(
            detection=detection,
            classifier_scores=scores,
            classifier_provider=self.classifier.name,
            fused=fused,
            policy_version=self.policy_version,
            policy_revision=self.policy_revision,
        )

    def quick(self, text: str, *, source: SourceType = SourceType.USER) -> EnrichedPipelineResult:
        """单段文本快捷检测（CLI / 测试用）。"""
        return self.run(
            [TaggedContent(content=text, source=source)],
            session_id=None,
        )
