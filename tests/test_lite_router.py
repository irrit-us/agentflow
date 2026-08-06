from __future__ import annotations

import httpx
import pytest

from agentflow.lite import Message, ModelProfile, ModelRouter
from agentflow.lite.client import LLMError


def _payload(content: str) -> dict:
    return {
        "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _profiles() -> list[ModelProfile]:
    return [
        ModelProfile(name="primary", model="m", base_url="http://primary/v1"),
        ModelProfile(name="backup", model="m", base_url="http://backup/v1"),
    ]


def test_profile_lookup_and_unknown_role():
    router = ModelRouter({"fast": _profiles()})

    assert router.profile("fast").name == "primary"
    with pytest.raises(KeyError) as exc_info:
        router.profile("missing")
    assert "fast" in str(exc_info.value)
    with pytest.raises(KeyError):
        router.chat("missing", [Message(role="user", content="hi")])


def test_chat_falls_back_after_primary_llm_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agentflow.lite.client.time.sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "primary":
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=_payload("from backup"))

    router = ModelRouter({"fast": _profiles()}, transport=httpx.MockTransport(handler))
    result = router.chat("fast", [Message(role="user", content="hi")])

    assert result.message.content == "from backup"


def test_chat_raises_last_error_when_all_profiles_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agentflow.lite.client.time.sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        status = 500 if request.url.host == "primary" else 503
        return httpx.Response(status, json={"error": {"message": request.url.host}})

    router = ModelRouter({"fast": _profiles()}, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as exc_info:
        router.chat("fast", [Message(role="user", content="hi")])

    assert exc_info.value.status_code == 503


def test_client_is_cached_per_profile():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=_payload("ok"))

    router = ModelRouter({"fast": _profiles()}, transport=httpx.MockTransport(handler))
    router.chat("fast", [Message(role="user", content="hi")])
    router.chat("fast", [Message(role="user", content="hi again")])

    assert requests == 2
    assert len(router._clients) == 1
    assert "primary" in router._clients


def test_profile_defaults_merge_and_explicit_kwargs_win():
    seen_bodies: list[dict] = []
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_payload("ok"))

    profiles = [
        ModelProfile(
            name="tuned",
            model="m",
            base_url="http://primary/v1",
            temperature=0.2,
            max_tokens=64,
        )
    ]
    router = ModelRouter({"fast": profiles}, transport=httpx.MockTransport(handler))

    router.chat("fast", [Message(role="user", content="hi")])
    router.chat("fast", [Message(role="user", content="hi")], temperature=0.9)

    assert seen_bodies[0]["temperature"] == 0.2
    assert seen_bodies[0]["max_tokens"] == 64
    assert seen_bodies[1]["temperature"] == 0.9
