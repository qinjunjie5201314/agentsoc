"""C4 自然语言转策略 - 共享词典与文本提取工具。

设计原则：
- 纯函数，无外部依赖
- 中文 / 英文 / 拼音同义词全部用一份词典
- 抽出函数被 ``app/policy/nl2policy.py`` 与测试共用
"""
from __future__ import annotations

import re
from typing import Final

# =========================== 工具名词典 ===========================
# 形式：{canonical_name: 同义词集合}。canonical 用 builtin tool_executor / tool_schemas 的真实名
TOOL_NAMES: Final[dict[str, tuple[str, ...]]] = {
    "bash": (
        "bash", "shell", "sh", "terminal", "console", "command",
        "命令行", "终端", "控制台", "执行命令", "命令行工具", "终端工具",
        "命令行程序", "脚本执行",
    ),
    "read_file": (
        "read_file", "readfile", "cat", "open", "fetch_file", "load_file",
        "读取文件", "读文件", "打开文件", "读取", "看文件", "文件读取",
        "打开", "读盘",
    ),
    "write_file": (
        "write_file", "writefile", "save", "save_file", "create_file",
        "写文件", "保存文件", "写盘", "保存", "创建文件", "写入",
    ),
    "query_database": (
        "query_database", "query_database_sql", "db", "database", "sql",
        "查数据库", "查表", "数据库查询", "SQL 查询", "执行 SQL",
    ),
    "run_task": (
        "run_task", "task", "subtask", "agent", "spawn",
        "派任务", "运行任务", "启动任务", "调用 Agent", "派发",
    ),
    "send_email": (
        "send_email", "email", "mail", "smtp",
        "发邮件", "发邮件工具", "发送邮件", "发信",
    ),
    "http_get": (
        "http_get", "curl", "wget", "http", "fetch_url",
        "发送 GET 请求", "请求 URL", "发请求", "拉取", "调用接口", "http 请求",
    ),
    "get_weather": (
        "get_weather", "weather",
        "查天气", "天气", "查天气工具",
    ),
    "search": (
        "search", "lookup", "find", "query",
        "搜索", "查找", "检索", "查询",
    ),
    "execute_python": (
        "execute_python", "python", "py", "interpreter",
        "执行 Python", "运行 Python", "Python 执行", "Python 脚本",
    ),
}

# 反向索引：同义词（小写）-> canonical
_TOOL_ALIAS_INDEX: Final[dict[str, str]] = {}
for _canonical, _aliases in TOOL_NAMES.items():
    for _alias in _aliases:
        _key = _alias.lower().strip()
        # 不覆盖已存在（先注册的更优先）
        _TOOL_ALIAS_INDEX.setdefault(_key, _canonical)


# =========================== 意图正则 ===========================

# "禁止 X"、"禁用 X"、"拦截 X"、"block X"、"deny X" → block 意图
_RE_BLOCK = re.compile(
    r"(禁止|禁用|不许|不得|不要|不允许|拦截|拒[绝止])"
    r"|"
    r"\b(block|deny|reject|disallow|forbid|prohibit)\b",
    re.IGNORECASE,
)

# "允许 X"、"放行 X" → 弱化（不在 M1 范围，记作 unknown）
_RE_ALLOW = re.compile(
    r"(允许|放行|放过去|许可|准许)"
    r"|"
    r"\b(allow|permit|whitelist)\b",
    re.IGNORECASE,
)

# "加规则"、"新增规则"、"如果出现 X 就..."、"检测 X" → add_rule
_RE_ADD_RULE = re.compile(
    r"(加一条?规则|添加规则|新增规则|写一条?规则|写个规则|定义规则|配置规则"
    r"|如果(出现|包含|命中)|一旦(出现|包含|命中)|当 (prompt|消息|输入|prompt 中|prompt 里) (出现|包含|命中)|"
    r"只要(出现|包含|命中)|"
    r"拦截 prompt|检测 prompt|检测消息|扫描 |"
    r"包含.*?时\s*(block|拒|拦))"
    r"|"
    r"\b(add|create|new|define)\s+rule\b"
    r"|"
    r"\bif\s+.*?\s+(then\s+)?(block|reject|deny)\b",
    re.IGNORECASE,
)

# 路径提取（Linux/Windows，绝对路径 + 相对路径 + URL，且不允许裸词）
# 起点必须含以下特征之一：
#   /    Linux 绝对或一级根
#   ~/   home
#   \\   Windows 盘符前缀（C:\）
#   ://  URL
#   ./   显式相对
#   */   显式 glob（且必须包含 * 通配符）
# 路径提取（Linux/Windows，绝对路径 + 相对路径 + URL + glob）
# 起点必须显式包含路径标志之一：
#   /              Linux 绝对
#   ~/             home
#   ./ 或 ../      显式相对
#   <letter>:\     Windows 盘符
#   http:// / https://
#   /与* 都有      Linux glob（由第一支覆盖：/etc/*.conf）
_RE_PATH = re.compile(
    r"(?:~?/|\./|\.\./)[A-Za-z_0-9][\w./\-*?{}\[\]]*"            # /etc/passwd · /etc/*.conf · ./foo
    r"|"
    r"~[A-Za-z_0-9/][\w./\-*?{}\[\]]*"                            # ~/.ssh/  ~user/file
    r"|"
    r"[A-Za-z]:\\[A-Za-z0-9_.\\\-*?{} ]+"                           # C:\Windows\System32
    r"|"
    r"https?://[A-Za-z0-9._/\-:?#=&%]+"                             # http://x.example.com/y
)

# 关键词/模式提取：引号内容优先；否则从"出现 X"、"包含 X"里抽
_RE_QUOTED = re.compile(r"""[「"'`『]([^「"'`』\n]{2,80})[」"'`』]""")
_RE_BACKTICK = re.compile(r"```([^\n`]{2,200})```")  # 三反引号

# "rm -rf"、"DROP TABLE"、"os.system" 这种命令/函数名
_RE_DANGEROUS_CMD = re.compile(
    r"(rm\s+-rf|rm\s+-fr|drop\s+table|truncate\s+table|mkfs|dd\s+if=|"
    r"os\.system|subprocess|eval\s*\(|exec\s*\(|"
    r"delete\s+from|update\s+\w+\s+set)",
    re.IGNORECASE,
)

# "检测包含 X"、"关键词 X"、"命中 X"、"包含 X"、"出现 X" → 抽出 X 作为潜在 pattern
# 匹配两类：
#   - CamelCase / UPPER_SNAKE：PROJECT_X_CODENAME / ApiKey
#   - snake / lower：secrets / my_token
_RE_TOKEN_HINT = re.compile(
    r"(?:含|包含|检出|命中|出现|关键词|关键字|关键字是|pattern\s*(?:is)?|detect\s+)?"
    r"[\s:：]*"
    r"[\"'`「]?"
    r"([A-Z][A-Z0-9_]{2,}|[A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*|[a-z_][a-z0-9_]{2,})"
    r"[\"'`」]?",
    re.IGNORECASE,
)


# =========================== 抽取函数 ===========================


def extract_tools(text: str) -> list[str]:
    """从文本里抽出**已知**工具 canonical 名（去重，保持文本出现顺序）。

    实现：按最长 alias 优先扫描（避免短 alias 把长 alias 截断）。
    词边界规则：
      - alias 是英文：要求前后非字母数字
      - alias 是中文：直接 find（中文分词靠 alias 优先级，边界不严格）
    """
    seen: dict[str, None] = {}
    lower = text.lower()

    def _is_eng_alnum(ch: str) -> bool:
        return ch.isalnum() and ch.isascii()

    for alias in sorted(_TOOL_ALIAS_INDEX.keys(), key=len, reverse=True):
        canonical = _TOOL_ALIAS_INDEX[alias]
        if canonical in seen:
            continue
        idx = 0
        while True:
            pos = lower.find(alias, idx)
            if pos < 0:
                break
            end = pos + len(alias)
            alias_is_ascii = alias.isascii()
            if alias_is_ascii:
                before_ok = pos == 0 or not _is_eng_alnum(lower[pos - 1])
                after_ok = end >= len(lower) or not _is_eng_alnum(lower[end])
            else:
                # 中文 alias：宽松边界即可
                before_ok = True
                after_ok = True
            if before_ok and after_ok:
                seen.setdefault(canonical, None)
                break
            idx = pos + 1
    return list(seen.keys())


def extract_paths(text: str) -> list[str]:
    """从文本中提取路径片段（粗匹配，可能含边界噪声，调用方需去重/校验）。"""
    candidates: list[str] = []
    for m in _RE_PATH.finditer(text):
        p = m.group(0).rstrip(".,;:?")
        if p and p not in candidates:
            candidates.append(p)
    return candidates


def extract_quoted(text: str) -> list[str]:
    """抽取带引号 / 反引号的字符串字面值。"""
    out: list[str] = []
    for m in _RE_QUOTED.finditer(text):
        s = m.group(1).strip()
        if s and s not in out:
            out.append(s)
    for m in _RE_BACKTICK.finditer(text):
        s = m.group(1).strip()
        if s and s not in out:
            out.append(s)
    return out


def is_block_intent(text: str) -> bool:
    return bool(_RE_BLOCK.search(text)) and not _RE_ALLOW.search(text)


def looks_like_add_rule(text: str) -> bool:
    return bool(_RE_ADD_RULE.search(text))


def detect_dangerous_cmd(text: str) -> str | None:
    """返回第一条命令/函数命中（用于 'block bash 跑 rm -rf' 这种带具体命令的）。"""
    m = _RE_DANGEROUS_CMD.search(text)
    return m.group(0) if m else None


def extract_pattern_tokens(text: str) -> list[str]:
    """从"包含 X"、"关键词 X"等语境里抽出**可疑的 pattern token**。

    只挑 CamelCase / UPPER_SNAKE / 下划线开头的 token，避免抓"我们 / 这是"等普通中文词。
    """
    # 必须有显式引导词（避免抓无意义的中文 token）
    if not re.search(r"(含|包含|检出|命中|出现|关键词|关键字|关键词是|检测|扫描)", text):
        return []
    seen: dict[str, None] = {}
    for m in _RE_TOKEN_HINT.finditer(text):
        tok = m.group(1)
        # 过滤太短 / 全数字
        if len(tok) < 3 or tok.isdigit():
            continue
        # 过滤纯常见助词
        if tok.lower() in {"the", "and", "for", "with", "detect", "prompt", "message", "input"}:
            continue
        seen.setdefault(tok, None)
    return list(seen.keys())


def normalize_pattern_type(name: str) -> str:
    """把抽取出的"模式字面"分类（nl2policy 内部表示）：

    - ``keyword`` —— 普通关键词（最终映射到 ``keyword_any``）
    - ``regex``   —— 含正则元字符的（直接当 ``regex``）
    - ``path``    —— 路径形（如 /etc/*.conf），用 ``regex`` 实现

    注：``PatternType`` 枚举仅有 ``regex / keyword_any / keyword_all / length``，
    此函数只决定 NL 草稿在 nl2policy 阶段**如何表达**，最终序列化时再做一次映射。
    """
    if name.startswith("/") or "*" in name:
        return "path"
    if re.search(r"[\\^$.*+?|(){}\[\]]", name) and len(name) > 4:
        return "regex"
    return "keyword"


__all__ = [
    "TOOL_NAMES",
    "detect_dangerous_cmd",
    "extract_paths",
    "extract_pattern_tokens",
    "extract_quoted",
    "extract_tools",
    "is_block_intent",
    "looks_like_add_rule",
    "normalize_pattern_type",
]
