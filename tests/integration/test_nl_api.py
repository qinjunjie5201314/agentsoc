"""C4 NL 转策略 - HTTP 集成测试（FastAPI TestClient）。

覆盖：
  - GET /v1/policy/nl/examples
  - POST /v1/policy/nl/preview (各 intent 都过)
  - POST /v1/policy/nl/apply (落盘 + 触发 reload, 文件落在真实 tmpdir)
  - 防路径穿越 (filename 校验)
  - uncertain intent 拒绝 apply
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.policy import (
    PolicyRegistry,
    create_nl_router,
    nl_to_draft,
)
from app.policy.nl2policy import Intent

# =========================== Fixtures ===========================


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    """空 policies 目录，足够 registry 启动。"""
    (tmp_path / "empty.yaml").write_text("# placeholder\nrules: []\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(policy_dir: Path) -> TestClient:
    registry = PolicyRegistry(policy_dir)
    registry.reload(trigger="startup")
    app = FastAPI()
    app.include_router(create_nl_router(registry=registry, policy_dir=policy_dir))
    return TestClient(app)


# =========================== examples ===========================


class TestExamplesEndpoint:
    def test_examples_count(self, client: TestClient) -> None:
        r = client.get("/v1/policy/nl/examples")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 6
        assert all("text" in ex and "hint" in ex for ex in data["examples"])

    def test_examples_min_confidence(self, client: TestClient) -> None:
        r = client.get("/v1/policy/nl/examples")
        data = r.json()
        assert 0 <= data["min_confidence"] <= 1


# =========================== preview ===========================


class TestPreviewEndpoint:
    @pytest.mark.parametrize("text,expected_intent", [
        ("禁止调用 get_weather", "block_tool"),
        ("禁用 bash", "block_tool"),
        ("禁止用 bash 删除 /etc 下文件", "block_tool_arg_pattern"),
        ("block bash if it runs rm -rf", "block_tool_arg_pattern"),
        ("添加规则：检测包含 PROJECT_X_CODENAME 的 prompt", "add_rule"),
        ('拦截 prompt 中出现 "rm -rf /" 的请求', "add_rule"),
    ])
    def test_preview_intents(self, client: TestClient, text: str, expected_intent: str) -> None:
        r = client.post("/v1/policy/nl/preview", json={"text": text})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["draft"]["classification"]["intent"] == expected_intent
        assert 0 <= data["draft"]["classification"]["confidence"] <= 1

    def test_preview_includes_yaml_and_summary(self, client: TestClient) -> None:
        r = client.post("/v1/policy/nl/preview", json={"text": "禁止调用 bash"})
        data = r.json()
        d = data["draft"]
        assert d.get("yaml")
        assert "blocked_tools" in d["yaml"]
        assert d["summary"]

    def test_preview_uncertain_keeps_yaml_empty(self, client: TestClient) -> None:
        r = client.post("/v1/policy/nl/preview", json={"text": "今天天气怎么样"})
        assert r.status_code == 200
        data = r.json()
        d = data["draft"]
        # uncertain 时 ok=False（前端已标记），yaml 留空
        assert data["ok"] is False
        assert d["yaml"] == ""

    def test_preview_invalid_severity(self, client: TestClient) -> None:
        r = client.post("/v1/policy/nl/preview", json={"text": "禁用 bash", "severity": "unknown"})
        # pydantic 会拒绝 severity 不在枚举内的请求
        assert r.status_code in (400, 422)

    def test_preview_empty_text(self, client: TestClient) -> None:
        r = client.post("/v1/policy/nl/preview", json={"text": ""})
        assert r.status_code == 422


# =========================== apply ===========================


class TestApplyEndpoint:
    def test_apply_add_rule_writes_file_and_bumps_revision(
        self, client: TestClient, policy_dir: Path,
    ) -> None:
        # 直接通过 registry 拿当前 revision
        registry = PolicyRegistry(policy_dir)
        registry.reload()
        rev_before = registry.revision

        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "添加规则：检测包含 PROJECT_X_CODENAME 的 prompt"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["intent"] == "add_rule"
        assert body["reload"]["ok"] is True
        assert body["reload"]["revision"] > rev_before

        # 文件确实落盘
        target = policy_dir / body["filename"]
        assert target.is_file()
        content = target.read_text(encoding="utf-8")
        assert "PROJECT_X_CODENAME" in content
        assert "type: keyword_any" in content

    def test_apply_block_tool(self, client: TestClient, policy_dir: Path) -> None:
        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "禁用 get_weather", "severity": "high"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["intent"] == "block_tool"
        target = policy_dir / body["filename"]
        content = target.read_text(encoding="utf-8")
        assert "get_weather" in content
        assert "blocked_tools" in content

    def test_apply_block_tool_arg_pattern(self, client: TestClient, policy_dir: Path) -> None:
        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "禁止用 bash 删除 /etc 下文件"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["intent"] == "block_tool_arg_pattern"
        content = (policy_dir / body["filename"]).read_text(encoding="utf-8")
        assert "dangerous_patterns" in content
        assert "/etc" in content

    def test_apply_uncertain_returns_422(self, client: TestClient, policy_dir: Path) -> None:
        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "我想去看电影"},
        )
        assert r.status_code == 422, r.text

    def test_apply_rejects_path_traversal(self, client: TestClient, policy_dir: Path) -> None:
        # filename 包含 / 视为非法
        r = client.post(
            "/v1/policy/nl/apply",
            json={
                "text": "禁用 bash",
                "filename": "../escape.yaml",
            },
        )
        assert r.status_code == 400

    def test_apply_rejects_non_yaml_extension(self, client: TestClient, policy_dir: Path) -> None:
        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "禁用 bash", "filename": "evil.exe"},
        )
        assert r.status_code == 400

    def test_apply_uses_suggested_filename_when_omitted(self, client: TestClient) -> None:
        r = client.post(
            "/v1/policy/nl/apply",
            json={"text": "禁用 send_email"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["filename"].endswith(".yaml")
        assert body["filename"].startswith("nl_")


# =========================== 与 PolicyRegistry 协同 ===========================


class TestNLRegistryIntegration:
    """NL 生成的规则必须真的能被 registry 集成并加载（端到端可生效）。"""

    def test_generated_rule_loadable(self, policy_dir: Path) -> None:
        reg = PolicyRegistry(policy_dir)
        reg.reload()
        draft = nl_to_draft(
            "添加规则：检测包含 SECRET_API_KEY 的 prompt", registry=reg,
        )
        assert draft.classification.intent is Intent.ADD_RULE
        assert draft.validation.get("ok") is True

        # 实际写到 strategy 目录并 reload
        target = policy_dir / "nl_gen.yaml"
        target.write_text(draft.yaml_text, encoding="utf-8")
        result = reg.reload(trigger="test")
        assert result.ok is True
        rid = draft.structure["rules"][0]["id"]
        assert any(r.id == rid for r in reg.rules)
