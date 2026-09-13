"""L1 归一化器。

把输入内容统一为标准化纯文本，剥离各类用于绕过检测的编码/隐形字符。

支持 5 类归一化（按顺序执行，支持递归归一化）：
1. URL 编码还原：%XX、+ 空格
2. Unicode escape 还原：\\uXXXX、\\UXXXXXXXX、\\xXX
3. HTML entity 还原：&#xxx;、&#xXXXX;、&name;
4. Base64 检测与解码：长度 + 字符集启发式
5. 零宽字符/双向控制符移除：ZWJ/ZWS/ZWSP/BOM/RTL/LTR

所有处理均在 str 层进行，不假定输入已经是字符串。
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 最大递归归一化轮次（避免恶意死循环）
MAX_PASSES = 5


# ============ 字符集常量 ============

# 零宽字符与不可见控制符（要删除的）
_INVISIBLE_CHARS = frozenset(
    {
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\u202f",  # narrow no-break space
        "\u2060",  # word joiner
        "\u2061",  # function application
        "\u2062",  # invisible times
        "\u2063",  # invisible separator
        "\u2064",  # invisible plus
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first strong isolate
        "\u2069",  # pop directional isolate
        "\ufeff",  # BOM / zero-width no-break space
        "\ufff9",  # interlinear annotation anchor
        "\ufffa",  # interlinear annotation separator
        "\ufffb",  # interlinear annotation terminator
    }
)

# Base64 合法字符
_BASE64_ALPHABET = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
# 启发式最小长度（攻击 payload 一般不会更短）
_BASE64_MIN_LEN = 8
# 长度低于此值且不含 +/= 时，一律视为普通单词（credentials / administrator / instructions 都是 11-14 字符的英文词，
# 它们恰好全落在 base64 字母表内，误判后会输出乱码并破坏下游规则匹配）
_BASE64_UNAMBIGUOUS_LEN = 24


# ============ 单步归一化函数 ============

def decode_url(text: str) -> str:
    """URL 解码 - 支持 %XX 与 +（空格）。

    自动处理二次编码：解码后再解码直到稳定。
    """
    prev = None
    cur = text
    for _ in range(MAX_PASSES):
        if cur == prev:
            break
        prev = cur
        cur = urllib.parse.unquote_plus(cur)
    return cur


def decode_unicode_escape(text: str) -> str:
    """还原 Unicode/十六进制/八进制 escape 序列。

    支持：
    - \\uXXXX
    - \\UXXXXXXXX
    - \\xXX
    - \\NNN（八进制，3 位）
    """
    # \uXXXX / \UXXXXXXXX
    # group(1) 命中 \uXXXX 分支，group(0) 命中 \UXXXXXXXX 分支
    text = re.sub(
        r"\\U[0-9a-fA-F]{8}|\\u\{?([0-9a-fA-F]{1,6})\}?",
        lambda m: chr(int(m.group(1) or m.group(0)[2:], 16)),
        text,
    )
    # \xXX
    text = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda m: chr(int(m.group(1), 16)),
        text,
    )
    # \NNN 八进制
    text = re.sub(
        r"\\([0-7]{3})",
        lambda m: chr(int(m.group(1), 8)) if int(m.group(1), 8) < 0x110000 else m.group(0),
        text,
    )
    return text


def decode_html_entity(text: str) -> str:
    """HTML 实体还原：&#xxx; &#xXXXX; &name;"""
    return html.unescape(text)


def strip_invisibles(text: str) -> str:
    """移除零宽字符与双向控制符。"""
    if not any(c in _INVISIBLE_CHARS for c in text):
        return text
    return "".join(c for c in text if c not in _INVISIBLE_CHARS)


def looks_like_base64(s: str) -> bool:
    """判断一个 token 是否值得尝试 Base64 解码。

    三重门槛（缺一不可），目的是**宁可漏判也不能误伤正常文本**：
      1. 长度 >= 8 且只含 base64 字符集
      2. 含 ``+`` / ``/`` / ``=`` 之一，或长度 >= 24
         —— 短且纯字母数字的串几乎都是英文单词（``credentials``、
         ``administrator``、``implementation``），解码后必然是乱码
      3. 长度是 4 的倍数，或补齐 padding 后能整除
    """
    if len(s) < _BASE64_MIN_LEN:
        return False
    if not _BASE64_ALPHABET.match(s):
        return False
    # 短且不含 +/= 的纯字母数字串：几乎都是英文单词，直接放行（不解码）
    has_special = any(c in "+/=" for c in s)
    if has_special:
        return True
    return len(s) >= _BASE64_UNAMBIGUOUS_LEN


def _decode_as_plausible_text(decoded: bytes) -> str | None:
    """解码结果必须"像文本"才采纳，否则返回 None。

    比原来的实现严格得多：
      - 优先 UTF-8 严格解码；失败则只接受纯 ASCII
      - 不允许出现任何 C0 控制字符（``\\x0b`` 这类噪声会破坏下游匹配）
      - 可读字符（字母数字/空白/常见标点）占比必须 >= 0.8
    """
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = decoded.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not text:
        return None
    # C0 控制字符（除常见空白）说明是二进制噪声
    if any(ord(c) < 0x20 and c not in "\n\r\t" for c in text):
        return None
    readable = sum(
        1
        for c in text
        if c.isalnum() or c in " \t\n\r.,;:!?-_/\\@#$%^&*()[]{}\"'=+<>|~`"
    )
    if readable / len(text) < 0.8:
        return None
    return text


def try_decode_base64_segment(segment: str) -> str | None:
    """尝试 Base64 解码单个 segment，成功返回解码后字符串，失败返回 None。"""
    s = segment.strip()
    if not looks_like_base64(s):
        return None
    # padding 修正
    padding = (-len(s)) % 4
    s_padded = s + "=" * padding
    try:
        decoded = base64.b64decode(s_padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return _decode_as_plausible_text(decoded)


def decode_base64(text: str) -> str:
    """扫描文本中疑似 Base64 片段并尝试解码。

    不会替换看起来不像 base64 的内容（如短词、空格分隔的句子）。
    """
    # 匹配独立 base64 段：前后是空白/标点/字符串边界
    pattern = re.compile(r"(?:^|(?<=[\s,;:\.\(\)\[\]\"']))([A-Za-z0-9+/]+={0,2})(?=$|(?=[\s,;:\.\(\)\[\]\"']))")

    def _repl(m: re.Match[str]) -> str:
        segment = m.group(1)
        decoded = try_decode_base64_segment(segment)
        return decoded if decoded is not None else segment

    return pattern.sub(_repl, text)


# ============ 编排 ============

@dataclass
class NormalizeResult:
    """归一化结果 + 元信息。"""

    original: str
    normalized: str
    changed: bool
    passes: int
    decoded_segments: list[dict[str, Any]] = field(default_factory=list)
    stripped_invisibles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized": self.normalized,
            "changed": self.changed,
            "passes": self.passes,
            "decoded_segments": self.decoded_segments,
            "stripped_invisibles": self.stripped_invisibles,
        }


def normalize(text: str) -> str:
    """便捷接口 - 只返回归一化后的字符串。"""
    return normalize_full(text).normalized


def normalize_full(text: str) -> NormalizeResult:
    """完整归一化流程，按顺序执行 5 类处理并支持递归。

    递归终止条件：连续两轮无变化 或 达到 MAX_PASSES。
    """
    if not text:
        return NormalizeResult(
            original=text,
            normalized=text,
            changed=False,
            passes=0,
        )

    cur = text
    invisibles_stripped = 0
    decoded_b64: list[dict[str, Any]] = []

    for pass_idx in range(1, MAX_PASSES + 1):
        prev = cur

        # 1. URL 解码
        cur = decode_url(cur)
        # 2. Unicode escape
        cur = decode_unicode_escape(cur)
        # 3. HTML entity
        cur = decode_html_entity(cur)
        # 4. Base64（记录解码位置）
        before_b64 = cur
        cur = decode_base64(cur)
        if cur != before_b64:
            decoded_b64.append({"pass": pass_idx, "preview": cur[:200]})
        # 5. 零宽字符
        stripped_count = sum(1 for c in cur if c in _INVISIBLE_CHARS)
        if stripped_count:
            invisibles_stripped += stripped_count
        cur = strip_invisibles(cur)

        if cur == prev:
            break

    return NormalizeResult(
        original=text,
        normalized=cur,
        changed=(cur != text),
        passes=pass_idx,
        decoded_segments=decoded_b64,
        stripped_invisibles=invisibles_stripped,
    )
