"""B6 L4 输出兜底的单元测试。"""

from __future__ import annotations

from app.audit.models import RiskAction, RiskLevel
from app.detection.egress import ToolPolicy, check_tool_call, load_tool_policy


def make_policy() -> ToolPolicy:
    return ToolPolicy.from_dict(
        {
            "blocked_tools": ["eval", "exec", "drop_database", "bash"],
            "allowlist": [],
            "dangerous_patterns": [
                {"name": "rm_rf", "pattern": r"\brm\s+-[a-z]*r[a-z]*f\b", "message": "危险删除"},
                {"name": "drop_table", "pattern": r"\bdrop\s+(database|table)\b", "message": "删库"},
                {"name": "select_star", "pattern": r"\bselect\s+\*\s+from\b", "message": "全表查询"},
            ],
        }
    )


# ---------- 基本放行 ----------


class TestAllow:
    def test_normal_tool_dict_args(self):
        d = check_tool_call("search", {"query": "weather"}, policy=make_policy())
        assert d.passed
        assert d.action is RiskAction.ALLOW
        assert d.risk_level is RiskLevel.LOW
        assert d.reason == ""

    def test_normal_tool_list_args(self):
        d = check_tool_call("search", ["a", "b"], policy=make_policy())
        assert d.passed

    def test_valid_json_string_args(self):
        d = check_tool_call("search", '{"query": "hello"}', policy=make_policy())
        assert d.passed
        assert d.arguments == {"query": "hello"}


# ---------- 黑名单 / 白名单 ----------


class TestBlocklist:
    def test_blocked_tool(self):
        d = check_tool_call("eval", {"code": "1+1"}, policy=make_policy())
        assert not d.passed
        assert d.action is RiskAction.BLOCK
        assert "blocklist" in d.reason

    def test_blocked_tool_case_insensitive(self):
        d = check_tool_call("BASH", {"cmd": "ls"}, policy=make_policy())
        assert not d.passed

    def test_allowlist_only_allows_listed(self):
        policy = make_policy()
        policy.allowlist = ["search", "read_file"]
        d = check_tool_call("write_file", {"path": "x"}, policy=policy)
        assert not d.passed
        assert "allowlist" in d.reason

    def test_allowlist_passes_listed_tool(self):
        policy = make_policy()
        policy.allowlist = ["search", "read_file"]
        d = check_tool_call("search", {"q": "x"}, policy=policy)
        assert d.passed


# ---------- 高危语义 ----------


class TestDangerousArgs:
    def test_rm_rf_in_args(self):
        d = check_tool_call("run", {"cmd": "rm -rf /"}, policy=make_policy())
        assert not d.passed
        assert "dangerous_args" in d.reason

    def test_drop_table_in_args(self):
        d = check_tool_call("run_sql", {"sql": "DROP TABLE users"}, policy=make_policy())
        assert not d.passed

    def test_select_star_in_args(self):
        d = check_tool_call("query", {"sql": "SELECT * FROM users"}, policy=make_policy())
        assert not d.passed

    def test_base64_encoded_rm(self):
        # "rm -rf /" base64 编码后走私进参数，归一化后应被识别
        import base64

        payload = base64.b64encode(b"rm -rf /").decode()
        d = check_tool_call("run", {"cmd": f"decode: {payload}"}, policy=make_policy())
        # normalize 会解出 base64 段，命中 rm_rf
        assert not d.passed

    def test_dangerous_pattern_in_tool_name(self):
        # 工具名本身含危险命令
        d = check_tool_call("rm -rf /tmp", {}, policy=make_policy())
        assert not d.passed
        assert "tool_name" in d.reason


# ---------- JSON / Schema ----------


class TestJsonSchema:
    def test_invalid_json_string_blocked(self):
        d = check_tool_call("search", "{invalid json", policy=make_policy())
        assert not d.passed
        assert "json_valid" in d.reason

    def test_schema_missing_required(self):
        schema = {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }
        d = check_tool_call("read_file", {}, policy=make_policy(), schema=schema)
        assert not d.passed
        assert "schema_valid" in d.reason

    def test_schema_type_mismatch(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        d = check_tool_call("count_items", {"count": "not-a-number"}, policy=make_policy(), schema=schema)
        assert not d.passed

    def test_schema_passes(self):
        schema = {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        }
        d = check_tool_call("read_file", {"path": "/tmp/x"}, policy=make_policy(), schema=schema)
        assert d.passed

    def test_schema_enum(self):
        schema = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["read", "write"]}}}
        d = check_tool_call("open", {"mode": "delete"}, policy=make_policy(), schema=schema)
        assert not d.passed


# ---------- 策略加载 ----------


class TestPolicyLoading:
    def test_from_dict(self):
        p = make_policy()
        assert "eval" in p.blocked_tools
        assert len(p.dangerous_patterns) == 3

    def test_load_default_yaml(self):
        p = load_tool_policy()
        assert len(p.blocked_tools) >= 10
        assert len(p.dangerous_patterns) >= 5

    def test_load_default_yaml_blocks_eval(self):
        d = check_tool_call("eval", {"code": "1"}, policy=None)
        assert not d.passed


# ---------- to_dict ----------


class TestToDict:
    def test_shape(self):
        d = check_tool_call("eval", {"code": "1"}, policy=make_policy())
        payload = d.to_dict()
        assert payload["tool_name"] == "eval"
        assert payload["action"] == "block"
        assert payload["risk_level"] == "high"
        assert payload["passed"] is False
        assert isinstance(payload["reason"], str)
        assert isinstance(payload["checks"], list)
        assert all("name" in c and "passed" in c for c in payload["checks"])
