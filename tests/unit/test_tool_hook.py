"""C2 工具调用 Hook 单元测试 —— 提取器 / 守卫 / 执行器 / 回灌消息。"""

from __future__ import annotations

import json

import pytest

from app.audit.models import RiskAction, RiskLevel
from app.detection.egress import ToolPolicy
from app.proxy.mock_llm import MockLLM, parse_tool_hint
from app.proxy.tool_executor import DryRunToolExecutor
from app.proxy.tool_hook import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    ToolGuard,
    build_tool_result_error,
    build_tool_result_ok,
    extract_tool_calls,
    load_tool_schemas,
)


@pytest.fixture
def guard() -> ToolGuard:
    return ToolGuard()


@pytest.fixture
def executor() -> DryRunToolExecutor:
    return DryRunToolExecutor()


# ============ 提取器 ============


class TestExtractOpenAI:
    def test_tool_calls_array(self):
        payload = {
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
                }
            ]
        }
        calls = extract_tool_calls(payload)
        assert len(calls) == 1
        assert calls[0].name == "get_weather"
        assert calls[0].arguments == {"city": "北京"}
        assert calls[0].call_id == "call_abc"
        assert calls[0].protocol == PROTOCOL_OPENAI

    def test_full_response_body(self):
        payload = {
            "id": "chatcmpl-x",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "search", "arguments": '{"query": "x"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        calls = extract_tool_calls(payload)
        assert len(calls) == 1
        assert calls[0].name == "search"

    def test_legacy_function_call(self):
        payload = {"function_call": {"name": "bash", "arguments": '{"cmd": "ls"}'}}
        calls = extract_tool_calls(payload)
        assert len(calls) == 1
        assert calls[0].name == "bash"
        assert calls[0].arguments == {"cmd": "ls"}

    def test_multiple_calls(self):
        payload = {
            "tool_calls": [
                {"id": "1", "function": {"name": "a", "arguments": "{}"}},
                {"id": "2", "function": {"name": "b", "arguments": "{}"}},
            ]
        }
        calls = extract_tool_calls(payload)
        assert [c.name for c in calls] == ["a", "b"]

    def test_invalid_json_arguments_kept_as_string(self):
        payload = {"tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "not json"}}]}
        calls = extract_tool_calls(payload)
        assert calls[0].arguments == "not json"
        assert calls[0].raw_arguments == "not json"

    def test_missing_name_skipped(self):
        payload = {"tool_calls": [{"id": "1", "function": {"arguments": "{}"}}]}
        assert extract_tool_calls(payload) == []

    def test_empty_payload(self):
        assert extract_tool_calls(None) == []
        assert extract_tool_calls({}) == []


class TestExtractAnthropic:
    def test_content_tool_use(self):
        payload = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "让我查一下"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "query_database",
                    "input": {"sql": "SELECT 1"},
                },
            ],
        }
        calls = extract_tool_calls(payload, protocol=PROTOCOL_ANTHROPIC)
        assert len(calls) == 1
        assert calls[0].name == "query_database"
        assert calls[0].arguments == {"sql": "SELECT 1"}
        assert calls[0].protocol == PROTOCOL_ANTHROPIC

    def test_auto_detect_anthropic(self):
        payload = {
            "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]
        }
        calls = extract_tool_calls(payload)
        assert len(calls) == 1
        assert calls[0].protocol == PROTOCOL_ANTHROPIC

    def test_single_block(self):
        calls = extract_tool_calls(
            {"type": "tool_use", "id": "t1", "name": "search", "input": {"query": "q"}},
            protocol=PROTOCOL_ANTHROPIC,
        )
        assert len(calls) == 1

    def test_non_tool_use_blocks_ignored(self):
        payload = {"content": [{"type": "text", "text": "hi"}]}
        assert extract_tool_calls(payload, protocol=PROTOCOL_ANTHROPIC) == []


# ============ 守卫 ============


class TestToolGuard:
    def test_blocked_tool(self, guard: ToolGuard):
        result = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "bash", "arguments": "{}"}}]}
        )
        assert result.action is RiskAction.BLOCK
        assert result.risk_level is RiskLevel.HIGH
        assert len(result.blocked) == 1
        assert "blocklist" in result.reason

    def test_dangerous_args(self, guard: ToolGuard):
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {"name": "run_task", "arguments": '{"cmd": "rm -rf /"}'},
                    }
                ]
            }
        )
        assert result.action is RiskAction.BLOCK
        assert "rm_rf" in result.reason

    def test_base64_obfuscated_args_caught(self, guard: ToolGuard):
        """参数里 Base64 走私 rm -rf，归一化后仍能命中。"""
        import base64

        encoded = base64.b64encode(b"rm -rf /").decode()
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {"id": "1", "function": {"name": "run_task", "arguments": json.dumps({"cmd": f"echo {encoded} | base64 -d | sh"})}},
                ]
            }
        )
        assert result.action is RiskAction.BLOCK

    def test_safe_tool_allowed(self, guard: ToolGuard):
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {"id": "1", "function": {"name": "get_weather", "arguments": '{"city": "上海"}'}}
                ]
            }
        )
        assert result.action is RiskAction.ALLOW
        assert len(result.allowed) == 1
        assert result.reason == ""

    def test_schema_validation_fails(self, guard: ToolGuard):
        """search 缺少必填 query → schema 校验失败。"""
        result = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "search", "arguments": "{}"}}]}
        )
        assert result.action is RiskAction.BLOCK
        assert "schema_valid" in result.reason
        assert "query" in result.reason

    def test_schema_type_mismatch(self, guard: ToolGuard):
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {"id": "1", "function": {"name": "search", "arguments": '{"query": 123}'}}
                ]
            }
        )
        assert result.action is RiskAction.BLOCK
        assert "字符串" in result.reason

    def test_partial_block_marks_overall_block(self, guard: ToolGuard):
        """混合调用：一个安全一个危险 → 整体 BLOCK，但安全的仍可执行。"""
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {"id": "1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
                    {"id": "2", "function": {"name": "bash", "arguments": "{}"}},
                ]
            }
        )
        assert result.action is RiskAction.BLOCK
        assert len(result.allowed) == 1
        assert len(result.blocked) == 1

    def test_no_calls_is_allow(self, guard: ToolGuard):
        result = guard.guard_payload({})
        assert result.action is RiskAction.ALLOW
        assert not result.has_calls
        assert not result.all_blocked

    def test_to_dict_shape(self, guard: ToolGuard):
        result = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "bash", "arguments": "{}"}}]}
        )
        d = result.to_dict()
        assert d["total"] == 1
        assert d["blocked_count"] == 1
        assert d["allowed_count"] == 0
        assert d["action"] == "block"
        assert isinstance(d["calls"], list)
        assert d["calls"][0]["checks"]

    def test_allowlist_mode(self):
        policy = ToolPolicy(allowlist=["get_weather"])
        g = ToolGuard(policy=policy, schemas={})
        blocked = g.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "search", "arguments": "{}"}}]}
        )
        allowed = g.guard_payload(
            {"tool_calls": [{"id": "2", "function": {"name": "get_weather", "arguments": "{}"}}]}
        )
        assert blocked.action is RiskAction.BLOCK
        assert "白名单" in blocked.reason
        assert allowed.action is RiskAction.ALLOW

    def test_unknown_tool_no_schema_requirement(self, guard: ToolGuard):
        """未在 tool_schemas 里的工具不强制 schema 校验。"""
        result = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "custom_tool", "arguments": "{}"}}]}
        )
        assert result.action is RiskAction.ALLOW

    def test_level_ranking(self, guard: ToolGuard):
        """多工具时取最高风险等级。"""
        result = guard.guard_payload(
            {
                "tool_calls": [
                    {"id": "1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
                    {"id": "2", "function": {"name": "bash", "arguments": "{}"}},
                ]
            }
        )
        assert result.risk_level is RiskLevel.HIGH


class TestLoadSchemas:
    def test_loads_from_policy_file(self):
        schemas = load_tool_schemas()
        assert "search" in schemas
        assert schemas["search"]["required"] == ["query"]

    def test_missing_file_returns_empty(self):
        assert load_tool_schemas("nonexistent-file.yaml") == {}


# ============ 回灌消息 ============


class TestToolResultBuilders:
    def test_openai_error_result(self, guard: ToolGuard):
        r = guard.guard_payload(
            {"tool_calls": [{"id": "call_9", "function": {"name": "bash", "arguments": "{}"}}]}
        )
        msg = build_tool_result_error(r.blocked[0])
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_9"
        assert msg["name"] == "bash"
        body = json.loads(msg["content"])
        assert body["error"] == "blocked_by_agentsentry"
        assert body["tool"] == "bash"

    def test_anthropic_error_result(self, guard: ToolGuard):
        r = guard.guard_payload(
            {"content": [{"type": "tool_use", "id": "toolu_9", "name": "bash", "input": {}}]}
        )
        msg = build_tool_result_error(r.blocked[0])
        assert msg["type"] == "tool_result"
        assert msg["tool_use_id"] == "toolu_9"
        assert msg["is_error"] is True

    def test_ok_result_dict_output(self, guard: ToolGuard):
        r = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]}
        )
        msg = build_tool_result_ok(r.allowed[0], {"temp": 25})
        assert msg["role"] == "tool"
        assert json.loads(msg["content"]) == {"temp": 25}

    def test_ok_result_string_output(self, guard: ToolGuard):
        r = guard.guard_payload(
            {"tool_calls": [{"id": "1", "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]}
        )
        msg = build_tool_result_ok(r.allowed[0], "plain text")
        assert msg["content"] == "plain text"


# ============ 执行器 ============


class TestDryRunExecutor:
    def test_known_tool(self, executor: DryRunToolExecutor):
        result = executor.execute("get_weather", {"city": "北京"})
        assert result.ok
        assert result.dry_run
        assert result.output["city"] == "北京"

    def test_unknown_tool(self, executor: DryRunToolExecutor):
        result = executor.execute("nonexistent_tool", {})
        assert not result.ok
        assert "未在 dry-run 执行器中注册" in result.error

    def test_search_tool(self, executor: DryRunToolExecutor):
        result = executor.execute("search", {"query": "AgentSoc"})
        assert result.ok
        assert result.output["total"] == 2

    def test_records_calls(self, executor: DryRunToolExecutor):
        executor.execute("search", {"query": "a"})
        executor.execute("read_file", {"path": "/tmp/x"})
        assert len(executor.calls) == 2
        executor.reset()
        assert executor.calls == []

    def test_as_text(self, executor: DryRunToolExecutor):
        ok = executor.execute("search", {"query": "a"})
        assert "results" in DryRunToolExecutor.as_text(ok)
        bad = executor.execute("nope", {})
        assert "error" in DryRunToolExecutor.as_text(bad)

    def test_execute_many(self, executor: DryRunToolExecutor):
        results = executor.execute_many([("search", {"query": "a"}), ("get_weather", {"city": "b"})])
        assert len(results) == 2

    def test_custom_mock_output(self):
        ex = DryRunToolExecutor({"my_tool": lambda a: {"echo": a}})
        result = ex.execute("my_tool", {"x": 1})
        assert result.ok
        assert result.output == {"echo": {"x": 1}}


# ============ Mock LLM 工具调用 ============


class TestMockLLMToolCalls:
    def test_parse_tool_hint(self):
        assert parse_tool_hint('查天气 [TOOL:get_weather] {"city": "北京"}') == (
            "get_weather",
            {"city": "北京"},
        )

    def test_parse_tool_hint_no_args(self):
        assert parse_tool_hint("执行 [TOOL:bash]") == ("bash", {})

    def test_parse_tool_hint_none(self):
        assert parse_tool_hint("普通消息") is None
        assert parse_tool_hint("") is None

    def test_llm_returns_tool_calls(self):
        llm = MockLLM()
        resp = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": '帮我 [TOOL:get_weather] {"city": "北京"}'}],
        )
        msg = resp["choices"][0]["message"]
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"}
        assert resp["choices"][0]["finish_reason"] == "tool_calls"

    def test_llm_summarizes_tool_results(self):
        llm = MockLLM()
        resp = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": "查天气"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "1", "name": "get_weather", "content": '{"temp": 25}'},
            ],
        )
        content = resp["choices"][0]["message"]["content"]
        assert "汇总" in content
        assert "get_weather" in content

    def test_llm_summarizes_blocked_tool(self):
        llm = MockLLM()
        blocked_payload = json.dumps(
            {"error": "blocked_by_agentsentry", "tool": "bash", "message": "工具在黑名单"}
        )
        resp = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": "删库"},
                {"role": "assistant", "content": None, "tool_calls": []},
                {"role": "tool", "tool_call_id": "1", "name": "bash", "content": blocked_payload},
            ],
        )
        content = resp["choices"][0]["message"]["content"]
        assert "拦截" in content
        assert "bash" in content

    def test_llm_normal_reply_without_hint(self):
        llm = MockLLM()
        resp = llm.chat_completion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "你好"}],
        )
        assert resp["choices"][0]["message"]["content"].startswith("[mock-")
        assert resp["choices"][0]["finish_reason"] == "stop"

    def test_stream_with_tool_calls(self):
        llm = MockLLM()
        chunks = list(
            llm.stream_chat_completion(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": '[TOOL:bash] {"cmd": "ls"}'}],
            )
        )
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
        assert any(c["choices"][0]["delta"].get("tool_calls") for c in chunks)
