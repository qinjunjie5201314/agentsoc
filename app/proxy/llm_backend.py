"""可插拔 LLM 后端（阶段 3 试点）。

把 Proxy 的「放行后调用哪个大模型」抽象成统一接口 ``LLMBackend``：

  - ``MockLLMBackend``：内置 MockLLM（回显，默认，不依赖外部）
  - ``OpenAICompatBackend``：转发到 OpenAI 兼容网关（内网模型网关 / 火山引擎 /
    DeepSeek / Qwen 等），同步 httpx 调用，支持流式（SSE）与工具调用透传

统一签名：``chat_completion(...) -> dict``（非流式）、``stream_chat_completion(...) -> Iterator``。
这样 ``openai_proxy.py`` 无需区分 mock / 真实后端，检测放行后直接透传即可。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from app.proxy.mock_llm import MockLLM

logger = logging.getLogger(__name__)


class LLMBackend:
    """后端抽象基类（鸭子类型，无需继承，只要实现两个方法）。"""

    name: str = "base"


class MockLLMBackend:
    """默认后端：MockLLM 回显，不依赖任何外部服务。"""

    name = "mock"

    def __init__(self, llm: MockLLM | None = None):
        self._llm = llm or MockLLM()

    def chat_completion(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> dict[str, Any]:
        return self._llm.chat_completion(model=model, messages=messages, tools=tools)

    def stream_chat_completion(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> Iterator[dict[str, Any]]:
        yield from self._llm.stream_chat_completion(model=model, messages=messages, tools=tools)


class OpenAICompatBackend:
    """转发到 OpenAI 兼容网关（内网模型网关等），同步 httpx 实现。

    - 非流式：POST /chat/completions，直接返回上游 JSON
    - 流式：透传 SSE（`stream=True`），逐 data 行 yield 出 dict
    - 工具调用：由上游模型自行产出 ``tool_calls``，Proxy 侧照常走 ToolGuard
    """

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def chat_completion(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> dict[str, Any]:
        import httpx

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self._url(), json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def stream_chat_completion(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> Iterator[dict[str, Any]]:
        import httpx

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream("POST", self._url(), json=payload, headers=self._headers()) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    yield chunk


def build_backend(backend: str = "mock", **kwargs: Any) -> Any:
    """按名字构建后端。

    Args:
        backend: "mock" | "openai"
        kwargs: 透传给具体后端构造器
    """
    b = backend.lower().strip()
    if b == "mock":
        return MockLLMBackend()
    if b == "openai":
        openai_kw = {
            k: v
            for k, v in kwargs.items()
            if k in ("base_url", "api_key", "model", "timeout")
        }
        return OpenAICompatBackend(**openai_kw)
    raise ValueError(f"未知 LLM backend: {backend!r}")


__all__ = [
    "LLMBackend",
    "MockLLMBackend",
    "OpenAICompatBackend",
    "build_backend",
]
