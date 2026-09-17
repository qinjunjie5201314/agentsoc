"""可插拔 LLM 后端（阶段 3）· 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.proxy.llm_backend import (
    MockLLMBackend,
    OpenAICompatBackend,
    build_backend,
)


class TestBuildBackend:
    def test_mock(self):
        b = build_backend("mock")
        assert isinstance(b, MockLLMBackend)

    def test_openai(self):
        b = build_backend("openai", base_url="http://gw/v1", api_key="k", model="m")
        assert isinstance(b, OpenAICompatBackend)
        assert b.base_url == "http://gw/v1"
        assert b.api_key == "k"
        assert b.model == "m"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            build_backend("nonexistent")


class TestMockLLMBackend:
    def test_chat_completion_echoes(self):
        b = MockLLMBackend()
        out = b.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
        assert out["choices"][0]["message"]["content"].startswith("[mock")

    def test_stream_yields_chunks(self):
        b = MockLLMBackend()
        chunks = list(
            b.stream_chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
        )
        assert chunks
        assert chunks[0]["object"] == "chat.completion.chunk"


class TestOpenAICompatBackend:
    def test_chat_completion_forwards(self):
        b = OpenAICompatBackend(base_url="http://gw/v1", api_key="k", model="m")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        fake_resp.raise_for_status.return_value = None
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.post.return_value = fake_resp
            out = b.chat_completion(model="m", messages=[{"role": "user", "content": "hi"}])
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_stream_yields_parsed_chunks(self):
        b = OpenAICompatBackend(base_url="http://gw/v1", api_key="k", model="m")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
            "data: [DONE]",
        ]
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = fake_resp
            chunks = list(b.stream_chat_completion(model="m", messages=[{"role": "user", "content": "x"}]))
        assert len(chunks) == 1
        assert chunks[0]["choices"][0]["delta"]["content"] == "hi"
