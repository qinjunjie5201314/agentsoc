"""Mock LLM —— C1/C2 用的占位大模型后端。

设计目标：
  - 不依赖任何外部 API（不烧钱、不联网）
  - 严格遵循 OpenAI ChatCompletion 响应形状
  - 简单回显用户最后一条消息的内容，证明"放行"路径已跑通
  - 流式按 token 切片输出（SSE 兼容）
  - **C2 新增**：支持返回 ``tool_calls``，用于演示「模型想调危险工具 → 被 L4 拦」

工具调用触发协议（可控、可预测）：
  在 user 消息里写 ``[TOOL:工具名] {json 参数}``，mock 就会返回对应的 tool_call。
  例：``帮我查下天气 [TOOL:get_weather] {"city": "北京"}``
  若请求带 ``tools`` 但消息里没有标记，则回退到普通文本回复（不猜）。

第二轮（messages 末尾是 role=tool）：
  mock 会把工具结果汇总成一段自然语言回复。若结果是 blocked 错误，
  回复会明确说明"我尝试调用了 X，但被安全策略拦截"。

后续 M2 替换为真实 LLM 客户端（litellm / openai SDK）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any

_TOOL_MARKER = re.compile(r"\[TOOL:?\s*([A-Za-z_][\w.-]*)\]\s*")


def _new_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _now() -> int:
    return int(time.time())


def _extract_json_object(s: str) -> dict[str, Any] | None:
    """从字符串里抓第一个平衡的 JSON 对象。"""
    s = s.strip()
    idx = s.find("{")
    if idx < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[idx:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_tool_hint(text: str) -> tuple[str, dict[str, Any]] | None:
    """解析 ``[TOOL:name] {args}`` 标记。"""
    if not text:
        return None
    m = _TOOL_MARKER.search(text)
    if not m:
        return None
    name = m.group(1)
    args = _extract_json_object(text[m.end() :]) or {}
    return name, args


class MockLLM:
    """简单的回显 LLM，支持 tool_calls。"""

    name = "mock"

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        completion_id = _new_id()
        created = _now()

        # ---- 情形 1：上一轮是工具结果 → 生成总结 ----
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if tool_msgs and messages and messages[-1].get("role") == "tool":
            reply = self._summarize_tool_results(tool_msgs)
            return self._text_response(completion_id, created, model, messages, reply)

        # ---- 情形 2：消息里有 [TOOL:xxx] 标记 → 返回 tool_calls ----
        last_user = self._last_user_text(messages)
        hint = parse_tool_hint(last_user)
        if hint:
            name, args = hint
            tc = {
                "id": f"call_{uuid.uuid4().hex[:20]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tc],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": self._usage(messages, ""),
            }

        # ---- 情形 3：普通文本回复 ----
        reply = self._build_reply(last_user, model)
        return self._text_response(completion_id, created, model, messages, reply)

    def stream_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[dict[str, Any]]:
        """按字符分块输出，模拟流式（SSE 友好）。"""
        full = self.chat_completion(model=model, messages=messages, tools=tools)
        message = full["choices"][0]["message"]
        reply: str = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        completion_id = full["id"]
        created = full["created"]

        # 首块：role
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
        }

        # 工具调用：整块吐出（简化，真实实现会增量拼 arguments）
        if tool_calls:
            for tc in tool_calls:
                yield {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [tc]},
                            "finish_reason": None,
                        }
                    ],
                }
            yield {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
            return

        # 逐字符块
        for ch in reply:
            yield {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": ch},
                        "finish_reason": None,
                    }
                ],
            }
        # 结束块
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    # ---- 内部工具 ----

    def _text_response(
        self,
        completion_id: str,
        created: int,
        model: str,
        messages: list[dict[str, Any]],
        reply: str,
    ) -> dict[str, Any]:
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": self._usage(messages, reply),
        }

    @staticmethod
    def _usage(messages: list[dict[str, Any]], reply: str) -> dict[str, int]:
        prompt_tokens = max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)
        completion_tokens = max(1, len(reply) // 4)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    @staticmethod
    def _last_user_text(messages: list[dict[str, Any]]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    texts = [p.get("text", "") for p in content if isinstance(p, dict)]
                    return "\n".join(texts)
                return ""
        return ""

    @staticmethod
    def _summarize_tool_results(tool_msgs: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for m in tool_msgs:
            name = m.get("name") or m.get("tool_call_id") or "tool"
            raw = m.get("content") or ""
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                payload = raw

            if isinstance(payload, dict) and payload.get("error") == "blocked_by_agentsentry":
                lines.append(
                    f"⛔ 我尝试调用工具 {payload.get('tool', name)}，"
                    f"但被 AgentSoc 安全策略拦截，未执行。\n"
                    f"   拦截原因：{payload.get('message', '')}\n"
                    f"   我将不会重试该操作，改为向你说明情况并等待指示。"
                )
            elif isinstance(payload, dict) and payload.get("error"):
                lines.append(f"⚠️ 工具 {name} 执行失败：{payload['error']}")
            else:
                preview = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
                lines.append(f"✅ 工具 {name} 执行成功，返回：{preview[:300]}")

        header = "[mock-agent] 工具调用轮结束，汇总如下：\n"
        return header + "\n".join(lines)

    @staticmethod
    def _build_reply(user_text: str, model: str) -> str:
        if not user_text:
            return "[mock] 你好，请在 messages 里加一条 role=user 的消息。"
        return (
            f"[mock-{model}] 已收到你的消息：\n"
            f"  {user_text[:200]}{'...' if len(user_text) > 200 else ''}\n"
            f"  （这是 AgentSoc 的 mock 后端，检测通过后透传到这层）"
        )


__all__ = ["MockLLM", "parse_tool_hint"]
