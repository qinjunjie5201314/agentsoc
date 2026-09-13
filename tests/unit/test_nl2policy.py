"""C4 NL 转策略 · 单元测试。

覆盖：
  - 意图分类（4 类 + uncertain）
  - 实体抽取（tools / paths / quoted / dangerous_cmd / pattern tokens）
  - 草稿生成（block_tool / block_tool_arg_pattern / add_rule）
  - YAML ↔ schema 适配（keyword → keyword_any、value 转 list、path → regex）
  - 预检与 fail-safe
"""
from __future__ import annotations

import pytest

from app.detection.schema import PatternType
from app.policy.intents import (
    extract_paths,
    extract_pattern_tokens,
    extract_quoted,
    extract_tools,
    is_block_intent,
    looks_like_add_rule,
    normalize_pattern_type,
)
from app.policy.nl2policy import (
    MIN_CONFIDENCE,
    Intent,
    _to_schema_pattern,
    nl_to_draft,
)

# =========================== intents.py ===========================


class TestExtractTools:
    @pytest.mark.parametrize("text,expected", [
        ("禁止调用 bash", ["bash"]),
        ("禁用 get_weather", ["get_weather"]),
        ("禁用命令行", ["bash"]),  # 中文同义词
        ("disable send_email", ["send_email"]),
        ("不识别 foo", []),
    ])
    def test_extract_tools(self, text, expected):
        assert extract_tools(text) == expected

    def test_extract_tools_case_insensitive(self):
        assert extract_tools("BLOCK Bash") == ["bash"]

    def test_extract_tools_dedup(self):
        assert extract_tools("bash 脚本 bash 工具") == ["bash"]


class TestExtractPaths:
    def test_extract_simple_paths(self):
        assert extract_paths("禁止写 /etc/passwd") == ["/etc/passwd"]

    def test_extract_multiple_paths(self):
        paths = extract_paths("/etc/passwd 和 /var/log 都不能写")
        assert "/etc/passwd" in paths
        assert "/var/log" in paths

    def test_extract_url(self):
        assert "https://attacker.example.com" in extract_paths("https://attacker.example.com")

    def test_extract_glob(self):
        assert "/etc/*.conf" in extract_paths("/etc/*.conf")

    def test_no_path_no_match(self):
        # 不含路径标记的纯中文不应抽到东西
        assert extract_paths("禁止调用 bash") == []

    def test_home_tilde(self):
        # 出现 ~/.ssh 应当被识别为 path
        paths = extract_paths("禁止访问 ~/.ssh/id_rsa")
        assert any("~/.ssh" in p for p in paths)


class TestExtractQuoted:
    def test_extract_double_quoted(self):
        assert extract_quoted('包含 "rm -rf" 的请求') == ["rm -rf"]

    def test_extract_chinese_quotes(self):
        assert extract_quoted('包含「PROJECT_X」的请求') == ["PROJECT_X"]

    def test_extract_backtick(self):
        assert extract_quoted("使用 `drop table` 关键字") == ["drop table"]


class TestBlockAndRuleIntent:
    def test_block_zh(self):
        assert is_block_intent("禁止调用 bash") is True
        assert is_block_intent("禁用 read_file") is True

    def test_block_en(self):
        assert is_block_intent("block bash if it runs rm -rf") is True

    def test_allow_overrides_block(self):
        assert is_block_intent("允许 send_email") is False

    def test_add_rule_patterns(self):
        assert looks_like_add_rule("添加规则：检测 X") is True
        assert looks_like_add_rule("如果出现 ABC 就拒绝") is True
        assert looks_like_add_rule("当 prompt 中包含 KEY 时 block") is True
        assert looks_like_add_rule("拦截 prompt 中出现 X 的请求") is True


class TestPatternTokens:
    def test_extract_camel_case_after_keyword(self):
        tokens = extract_pattern_tokens("检测包含 PROJECT_X_CODENAME 的 prompt")
        assert "PROJECT_X_CODENAME" in tokens

    def test_extract_snake_case(self):
        tokens = extract_pattern_tokens("如果出现 my_secret_token 就拒绝")
        assert "my_secret_token" in tokens

    def test_no_keyword_no_extract(self):
        # 没"包含 / 出现 / 命中"等引导词就不该抽
        tokens = extract_pattern_tokens("这是一个普通句子")
        assert tokens == []

    def test_filter_common_words(self):
        # 引导词 + 短助词不应被当 pattern
        tokens = extract_pattern_tokens("检测包含 the 的 prompt")
        assert "the" not in tokens


class TestNormalizePatternType:
    def test_path(self):
        assert normalize_pattern_type("/etc/passwd") == "path"
        assert normalize_pattern_type("/etc/*.conf") == "path"

    def test_regex(self):
        assert normalize_pattern_type("rm\\s+-rf") == "regex"

    def test_keyword_default(self):
        assert normalize_pattern_type("PROJECT_X") == "keyword"


# =========================== nl2policy.py: 适配器 ===========================


class TestToSchemaPattern:
    def test_keyword_to_keyword_any(self):
        out = _to_schema_pattern({"type": "keyword", "value": "PROJECT_X"})
        assert out["type"] == PatternType.KEYWORD_ANY.value
        assert out["value"] == ["PROJECT_X"]

    def test_regex_kept(self):
        out = _to_schema_pattern({"type": "regex", "value": "rm\\s+-rf"})
        assert out["type"] == PatternType.REGEX.value
        assert out["value"] == "rm\\s+-rf"

    def test_path_becomes_regex_escaped(self):
        out = _to_schema_pattern({"type": "path", "value": "/etc/file?*"})
        assert out["type"] == PatternType.REGEX.value
        # 路径里的正则元字符必须被 escape；普通斜杠 / 不需要
        import re as _re
        raw = "/etc/file?*"
        escaped = _re.escape(raw)
        assert out["value"] == escaped
        assert "\\?" in out["value"]  # 元字符 ? 必须被 escape


# =========================== nl2policy.py: 分类器 ===========================


class TestNlToDraftIntent:
    @pytest.mark.parametrize("text,expected_intent", [
        ("禁止调用 get_weather", Intent.BLOCK_TOOL),
        ("禁用 bash", Intent.BLOCK_TOOL),
        ("block send_email", Intent.BLOCK_TOOL),
        ("禁止用 bash 删除 /etc 下文件", Intent.BLOCK_TOOL_ARG_PATTERN),
        ('拦截 prompt 中出现 "rm -rf" 的请求', Intent.ADD_RULE),
        ("添加规则：检测包含 PROJECT_X_CODENAME 的 prompt", Intent.ADD_RULE),
        ("block bash if it runs rm -rf", Intent.BLOCK_TOOL_ARG_PATTERN),
        ("如果出现 my_secret_token 这种 token 就拒绝", Intent.ADD_RULE),
        ("天气怎么样", Intent.UNCERTAIN),  # 与策略无关
        ("随便聊聊天", Intent.UNCERTAIN),
    ])
    def test_intent(self, text, expected_intent):
        d = nl_to_draft(text)
        assert d.classification.intent is expected_intent


class TestNlToDraftYaml:
    def test_block_tool_yaml(self):
        d = nl_to_draft("禁止调用 get_weather")
        assert "blocked_tools:" in d.yaml_text
        assert "get_weather" in d.yaml_text

    def test_block_tool_arg_pattern_yaml(self):
        d = nl_to_draft("禁止用 bash 删除 /etc 下文件")
        assert "dangerous_patterns:" in d.yaml_text
        assert "/etc" in d.yaml_text  # 就算被 re.escape 也保留 /etc 可读

    def test_add_rule_yaml_uses_keyword_any(self):
        d = nl_to_draft('拦截 prompt 中出现 "PROJECT_X_CODENAME" 的请求')
        assert "type: keyword_any" in d.yaml_text
        # value 应该是 list 形式
        assert "- PROJECT_X_CODENAME" in d.yaml_text

    def test_validation_blocks_no_validation_missing_registry(self):
        # 不传 registry 时只走 YAML 语法 + schema 一致性检查（其实是 trust=True）
        # 我们的 _validate 在 registry=None 时只校验非空 + YAML 序列化
        d = nl_to_draft("禁用 bash")
        # 即使没 registry，也要满足 not_empty + yaml-able
        assert d.validation["ok"] is True

    def test_uncertain_has_empty_yaml(self):
        d = nl_to_draft("今天天气怎么样")
        assert d.classification.intent is Intent.UNCERTAIN
        assert d.yaml_text == ""


# =========================== MIN_CONFIDENCE 常量 ===========================


class TestMinConfidence:
    def test_min_confidence_in_range(self):
        assert 0.0 < MIN_CONFIDENCE < 1.0


# =========================== EXAMPLES ===========================


class TestExamplesFixture:
    def test_examples_count(self):
        from app.policy.nl2policy import EXAMPLES
        assert len(EXAMPLES) >= 6
        for ex in EXAMPLES:
            assert "text" in ex and "hint" in ex

    def test_examples_each_parseable(self):
        from app.policy.nl2policy import EXAMPLES
        for ex in EXAMPLES:
            d = nl_to_draft(ex["text"])
            # 至少应该识别出一个具体 intent（uncertain 也算可解析）
            assert d.classification.intent is not None
