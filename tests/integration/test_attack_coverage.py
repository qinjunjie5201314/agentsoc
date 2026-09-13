"""E1 攻击样例集 - 全链路回归 + 拦截覆盖率统计。

读取 ``tests/fixtures/attacks/attack_samples.json``，对每条样例跑 DetectionPipeline，
断言拦截结果符合预期（block/allow），并统计分类覆盖率。

这是 M1 的"验收基线"：任何改动若导致样例漏报/误报，这里会立刻暴露。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.detection.pipeline import DetectionPipeline
from app.detection.egress import check_tool_call

FIXTURE = Path(__file__).parent.parent / "fixtures" / "attacks" / "attack_samples.json"


@pytest.fixture(scope="module")
def samples():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def pipeline():
    return DetectionPipeline.from_yaml("policies/builtin_rules.yaml")


def _expected_to_action(expect: str) -> str:
    """把样例的 expect 字段映射到 pipeline 的 final_action。"""
    return "block" if expect == "block" else "allow"


# =========================== 逐条样例验证 ===========================


class TestPromptSamples:
    @pytest.mark.parametrize(
        "sample",
        [s for s in json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]],
        ids=lambda s: s["id"],
    )
    def test_sample(self, sample, pipeline):
        text = sample["text"]
        # 上下文溢出样例：按 pad_to 用长文本填充触发长度阈值
        if sample.get("pad_to"):
            text = ("padding " * (sample["pad_to"] // 8)) + " " + text

        result = pipeline.run(
            [{"role": "user", "content": text}],
            session_id=f"e1-{sample['id']}",
        )
        got_action = result.final_action.value
        want_action = _expected_to_action(sample["expect"])

        if sample["expect"] == "allow":
            # 正常输入必须放行（测误报）
            assert got_action == "allow", (
                f"[{sample['id']}] 正常输入被误拦：{sample['text'][:50]}"
            )
        else:
            # 攻击输入必须拦下（block 或 confirm 都算拦，confirm 在 M1 也按 block 处置）
            assert got_action in ("block", "confirm"), (
                f"[{sample['id']}] 攻击未拦截：{sample['text'][:50]} · 实际={got_action}"
            )
            # 若标注了预期规则，校验命中规则
            if sample.get("expected_rule"):
                hit_rules = {h.rule_id for h in result.hits}
                assert sample["expected_rule"] in hit_rules, (
                    f"[{sample['id']}] 预期命中 {sample['expected_rule']}，"
                    f"实际命中 {hit_rules}"
                )


class TestDangerousToolSamples:
    @pytest.mark.parametrize(
        "tool_sample",
        json.loads(FIXTURE.read_text(encoding="utf-8"))["dangerous_tools"],
        ids=lambda t: t["id"],
    )
    def test_tool(self, tool_sample):
        decision = check_tool_call(tool_sample["tool"], tool_sample["arguments"])
        if tool_sample["expect"] == "block":
            assert decision.action.value == "block", (
                f"[{tool_sample['id']}] 高危工具未拦截：{tool_sample['tool']}"
            )
        else:
            assert decision.action.value == "allow"


# =========================== 覆盖率统计 ===========================


class TestCoverage:
    def test_sample_count(self, samples):
        """样例集至少 30 条（E1 验收标准）。"""
        assert len(samples["samples"]) >= 30

    def test_category_coverage(self, samples):
        """七大类都要有样例。"""
        categories = {s["category"] for s in samples["samples"]}
        for cat in [
            "prompt_injection_direct",
            "prompt_injection_indirect",
            "data_exfiltration",
            "dangerous_action",
            "normalize_bypass",
            "context_overflow",
            "safe",
        ]:
            assert cat in categories, f"缺少类别：{cat}"

    def test_normalize_bypass_covers_5_types(self, samples):
        """归一化绕过样例要覆盖 URL/Unicode/HTML/Base64/零宽 5 类。"""
        norm_ids = [
            s["id"] for s in samples["samples"] if s["category"] == "normalize_bypass"
        ]
        # 通过 id 前缀校验 5 类都在
        for prefix in ["norm_url", "norm_unicode", "norm_html", "norm_b64", "norm_zwsp"]:
            assert any(i.startswith(prefix) for i in norm_ids), f"缺少归一化类型：{prefix}"

    def test_dangerous_tools_cover_3_types(self, samples):
        """高危工具样例要覆盖 bash/eval(执行)/system 等至少 3 类。"""
        tools = {t["tool"] for t in samples["dangerous_tools"]}
        assert len(tools) >= 3
