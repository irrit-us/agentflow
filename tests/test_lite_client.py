from __future__ import annotations

import json

import httpx
import pytest

from agentflow.lite import LLMError, LiteLLMClient, Message


def _chat_payload(content: str = "hello") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


def test_chat_parses_content_usage_finish_reason_and_model():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        return httpx.Response(200, json=_chat_payload("pong"))

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    result = client.chat([Message(role="user", content="hi")], model="test-model")

    assert result.message.role == "assistant"
    assert result.message.content == "pong"
    assert result.usage.prompt_tokens == 3
    assert result.usage.completion_tokens == 5
    assert result.usage.total_tokens == 8
    assert result.finish_reason == "stop"
    assert result.model == "test-model"


def test_chat_parses_tool_calls_arguments_json_to_dict():
    payload = _chat_payload()
    payload["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "broken", "arguments": "{not json"},
            },
        ],
    }
    payload["choices"][0]["finish_reason"] = "tool_calls"

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    result = client.chat([Message(role="user", content="hi")], model="test-model")

    assert result.message.tool_calls is not None
    assert len(result.message.tool_calls) == 2
    assert result.message.tool_calls[0].name == "get_weather"
    assert result.message.tool_calls[0].arguments == {"city": "Paris"}
    # Unparseable arguments fall back to an empty dict.
    assert result.message.tool_calls[1].arguments == {}


def test_chat_401_raises_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError) as exc_info:
        client.chat([Message(role="user", content="hi")], model="test-model")

    assert exc_info.value.status_code == 401
    assert "unauthorized" in str(exc_info.value)
    assert calls == 1


def test_chat_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agentflow.lite.client.time.sleep", lambda seconds: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=_chat_payload("recovered"))

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        transport=httpx.MockTransport(handler),
    )
    result = client.chat([Message(role="user", content="hi")], model="test-model")

    assert calls == 3
    assert result.message.content == "recovered"


def test_base_url_trailing_slash_is_normalized():
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json=_chat_payload())

    client = LiteLLMClient(
        base_url="http://testserver/v1/",
        transport=httpx.MockTransport(handler),
    )
    client.chat([Message(role="user", content="hi")], model="test-model")

    assert seen_paths == ["/v1/chat/completions"]


def test_api_key_env_is_used_when_no_explicit_key():
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_chat_payload())

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        api_key_env="LITE_TEST_API_KEY",
        transport=httpx.MockTransport(handler),
    )
    import os

    os.environ["LITE_TEST_API_KEY"] = "sk-from-env"
    try:
        # Key was resolved at construction time when env was unset; set a fresh client.
        client_with_key = LiteLLMClient(
            base_url="http://testserver/v1",
            api_key_env="LITE_TEST_API_KEY",
            transport=httpx.MockTransport(handler),
        )
        client_with_key.chat([Message(role="user", content="hi")], model="test-model")
    finally:
        del os.environ["LITE_TEST_API_KEY"]

    # The client constructed before the env var existed sends no Authorization header.
    client.chat([Message(role="user", content="hi")], model="test-model")
    assert seen_auth == ["Bearer sk-from-env", None]
