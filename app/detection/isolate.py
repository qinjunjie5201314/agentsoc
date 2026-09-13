"""M1 检测层 · L2 来源隔离。

设计的核心思路（参考 M1-开发计划.md §3.2）：

  L1 归一化解决"攻击者把恶意载荷藏起来"。
  L2 来源隔离解决"模型分不清谁是可信的，谁不是"。

具体落地：
  - 每段进入 LLM 的文本都带 `SourceType` 标签（system/user/tool）
  - 工具返回强制标记为 tool 源（**默认不可信**）
  - 多个片段拼接时，序列化标记（XML 风格）一起透传给下游模型
  - L3 规则引擎根据 source 选择性检查（system 不查、tool 必须查、user 标准查）

本模块只负责"打标 + 序列化"，不做实际拦截。拦截由 L3/L4 完成。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.audit.models import SourceType

# ============ 数据结构 ============

# OpenAI 风格的 role 集合
_OPENAI_ROLES = {"system", "user", "assistant", "tool", "function", "developer"}

# 来源与 role 的默认映射
_ROLE_TO_SOURCE = {
    "system": SourceType.SYSTEM,
    "developer": SourceType.SYSTEM,
    "user": SourceType.USER,
    "assistant": SourceType.USER,  # assistant 输出也是用户可控输入，归为 user 段
    "tool": SourceType.TOOL,
    "function": SourceType.TOOL,
}


@dataclass
class TaggedContent:
    """带来源标记的文本片段。

    Attributes:
        content: 原始文本（已 L1 归一化或未归一化均可，由调用方决定）
        source: 来源类型
        role: 原始 role（如 'system'、'user'），可空
        metadata: 附加元数据（tool_name、tool_call_id、user_id 等）
    """

    content: str
    source: SourceType
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source": self.source.value,
            "role": self.role,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaggedContent:
        return cls(
            content=data["content"],
            source=SourceType(data["source"]),
            role=data.get("role"),
            metadata=data.get("metadata") or {},
        )


# ============ OpenAI 消息打标 ============

# role 字段到 SourceType 的转换（暴露为公开名，便于其它模块复用）
def infer_source_from_role(role: str | None) -> SourceType:
    """根据 OpenAI 风格 role 推断来源。"""
    if role is None:
        return SourceType.UNKNOWN
    return _ROLE_TO_SOURCE.get(role.lower(), SourceType.UNKNOWN)


def tag_messages(
    messages: Iterable[dict[str, Any]],
    *,
    tool_message_marker: SourceType | None = None,
) -> list[TaggedContent]:
    """把 OpenAI 风格 messages 打上来源标签。

    每条 message 的 content 字段支持：
      - str：直接打标
      - list[dict]（多模态）：每个 part 单独打一个 TaggedContent（共享 role/source）

    Args:
        messages: OpenAI 格式 messages，例 [{\"role\":\"system\",\"content\":\"...\"}]
        tool_message_marker: 强制覆盖 tool 消息的来源（默认用 role 推断）

    Returns:
        与原消息等长或略多（多模态情况下）的 TaggedContent 列表
    """
    result: list[TaggedContent] = []
    for msg in messages:
        role = msg.get("role")
        source = infer_source_from_role(role)
        if role in ("tool", "function") and tool_message_marker is not None:
            source = tool_message_marker

        content = msg.get("content")

        # 元数据：tool_call_id / tool name（仅 tool 消息）
        metadata: dict[str, Any] = {}
        if msg.get("tool_call_id"):
            metadata["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            metadata["name"] = msg["name"]
        if msg.get("tool_calls"):
            # assistant 主动调用工具的工具列表：不算"工具返回"，归 user
            metadata["tool_calls"] = msg["tool_calls"]

        if isinstance(content, str):
            result.append(TaggedContent(content=content, source=source, role=role, metadata=metadata))
        elif isinstance(content, list):
            # 多模态：每个 part 单独打标
            for part in content:
                if not isinstance(part, dict):
                    continue
                # 多模态部分（image_url 等）通常与文本独立处理，此处保留类型信息
                part_meta = dict(metadata)
                part_meta["part_type"] = part.get("type", "text")
                result.append(
                    TaggedContent(
                        content=json.dumps(part, ensure_ascii=False) if part.get("type") != "text" else part.get("text", ""),
                        source=source,
                        role=role,
                        metadata=part_meta,
                    )
                )
        elif content is None:
            # 部分 tool 消息 content 可为 None（如纯 tool_calls）
            result.append(TaggedContent(content="", source=source, role=role, metadata=metadata))
        else:
            # 未知类型，保险起见转字符串
            result.append(
                TaggedContent(content=str(content), source=source, role=role, metadata=metadata)
            )
    return result


def tag_tool_result(
    tool_name: str,
    content: str,
    *,
    tool_call_id: str | None = None,
) -> TaggedContent:
    """给工具返回结果打 tag（强制 SourceType.TOOL）。"""
    metadata: dict[str, Any] = {"tool_name": tool_name}
    if tool_call_id is not None:
        metadata["tool_call_id"] = tool_call_id
    return TaggedContent(
        content=content,
        source=SourceType.TOOL,
        role="tool",
        metadata=metadata,
    )


# ============ 序列化（透传至下游 LLM） ============

# 用 XML 风格标记，明确区分来源，又能被模型正确解析
_MARKER_RE = re.compile(r"<\|source:([^|>]+)\|>(.*?)<\|/source\|>", re.DOTALL)


def serialize_for_prompt(tagged: TaggedContent) -> str:
    """序列化为带 source 标记的文本片段。

    格式: <|source:user|>...内容...<|/source|>

    这种格式有两个优点：
      1. 模型能识别 source 边界，便于按段分别处理
      2. 反序列化时可严格还原，不会和正文混淆
    """
    # 移除内容中可能误触发结束标记的字符（理论上不会发生，但保险）
    safe = tagged.content.replace("<|/source|>", "<|/source⏎|>")
    return f"<|source:{tagged.source.value}|>{safe}<|/source|>"


def deserialize_from_prompt(text: str) -> list[TaggedContent]:
    """从带 source 标记的文本还原 TaggedContent 列表。"""
    result: list[TaggedContent] = []
    last_end = 0
    for match in _MARKER_RE.finditer(text):
        # 标记前的空白/纯文本，归为 UNKNOWN
        if match.start() > last_end:
            between = text[last_end : match.start()]
            if between.strip():
                result.append(TaggedContent(content=between, source=SourceType.UNKNOWN))

        source_str = match.group(1)
        content = match.group(2).replace("<|/source⏎|>", "<|/source|>")
        try:
            source = SourceType(source_str)
        except ValueError:
            source = SourceType.UNKNOWN
        result.append(TaggedContent(content=content, source=source))
        last_end = match.end()

    if last_end < len(text):
        tail = text[last_end:]
        if tail.strip():
            result.append(TaggedContent(content=tail, source=SourceType.UNKNOWN))
    return result


def compose_with_tags(parts: Iterable[TaggedContent]) -> str:
    """把多个 TaggedContent 拼接成一段完整 prompt。

    拼接顺序与输入顺序一致。每个片段前增加 source 标记，便于模型按段理解。
    """
    return "\n\n".join(serialize_for_prompt(p) for p in parts)


# ============ 下游（L3 规则引擎用）辅助 ============

def flatten_for_detection(
    tagged: Iterable[TaggedContent],
) -> list[tuple[SourceType, str]]:
    """展平 TaggedContent 为 (source, content) 元组列表，给 L3 规则引擎扫。

    注意：保留 source 信息，规则可以按来源选择性触发（如：tool 源强制做高危语义检查）。
    """
    return [(t.source, t.content) for t in tagged]


def segment_by_source(
    tagged: Iterable[TaggedContent],
) -> dict[SourceType, list[str]]:
    """按来源把内容分组，用于规则引擎对 user/tool 分别做检查。"""
    out: dict[SourceType, list[str]] = {s: [] for s in SourceType}
    for t in tagged:
        out[t.source].append(t.content)
    # 移除空组
    return {s: chunks for s, chunks in out.items() if chunks}


def extract_untrusted(tagged: Iterable[TaggedContent]) -> list[TaggedContent]:
    """提取所有不可信来源（user + tool）的内容。

    system 段默认完全可信，跳过。
    """
    return [t for t in tagged if t.source in (SourceType.USER, SourceType.TOOL, SourceType.UNKNOWN)]


def tag_segments_to_str(tagged_list: list[TaggedContent]) -> list[str]:
    """简单展示用的格式化（CLI 终端 / 日志输出）。"""
    return [f"[{t.source.value}] {t.content[:60]}{'...' if len(t.content) > 60 else ''}" for t in tagged_list]
