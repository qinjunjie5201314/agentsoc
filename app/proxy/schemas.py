"""OpenAI 兼容的请求/响应 Pydantic 模型。

C1 不强制调用方传标准模型（Pydantic 校验），允许部分字段缺失以兼容
"野生"客户端。但响应严格遵循 OpenAI ChatCompletion 形状，方便调用方
无感切换（仅 Base URL 改成 AgentSoc Proxy 即可）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """OpenAI 风格单条消息。"""

    role: str
    content: str | list[Any] | None = None
    name: str | None = None
    tool_call_id: str | None = None


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON 字符串


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatCompletionRequest(BaseModel):
    """OpenAI ChatCompletion 请求的最小可识别子集。"""

    model: str = "gpt-4o-mini"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    session_id: str | None = None  # 透传 AgentSoc session_id（可选）


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)
    # AgentSoc 附加字段（不影响 OpenAI 客户端）
    agentsentry: dict[str, Any] = Field(default_factory=dict)


class ChatCompletionChunkDelta(BaseModel):
    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
    agentsentry: dict[str, Any] = Field(default_factory=dict)


class AgentsentryErrorBody(BaseModel):
    """被拦截时返回的错误体（HTTP 400 / 403）。"""

    error: dict[str, Any]
    agentsentry: dict[str, Any]
