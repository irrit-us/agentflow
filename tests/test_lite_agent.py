from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from agentflow.lite import (
    BudgetExceededError,
    LiteAgent,
    LiteLLMClient,
    Message,
    tool,
)


def _response(content: str | None, tool_calls: list[dict] | None = None, usage: dict | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _client_with_queue(responses: list[dict]) -> LiteLLMClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert queue, "unexpected extra request"
        return httpx.Response(200, json=queue.pop(0))

    return LiteLLMClient(base_url="http://testserver/v1", transport=httpx.MockTransport(handler))


def test_constructor_validates_backend_configuration_and_runtime_limits():
    client = _client_with_queue([])

    with pytest.raises(ValueError, match="exactly one"):
        LiteAgent()
    with pytest.raises(ValueError, match="exactly one"):
        LiteAgent(client=client, router=object(), model="test-model", role="test-role")
    with pytest.raises(ValueError, match="role.*required"):
        LiteAgent(router=object())
    with pytest.raises(ValueError, match="model.*required"):
        LiteAgent(client=client)
    with pytest.raises(ValueError, match="max_iterations must be at least 1"):
        LiteAgent(client=client, model="test-model", max_iterations=0)
    with pytest.raises(ValueError, match="max_tool_iterations must be non-negative"):
        LiteAgent(client=client, model="test-model", max_tool_iterations=-1)
    with pytest.raises(ValueError, match="max_total_tokens must be at least 1"):
        LiteAgent(client=client, model="test-model", max_total_tokens=0)


def test_run_single_turn_without_tools():
    client = _client_with_queue([_response("done")])
    agent = LiteAgent(client=client, model="test-model", system_prompt="be brief")

    result = agent.run("hello")

    assert result.text == "done"
    assert result.iterations == 1
    assert result.finish_reason == "stop"
    assert result.usage.total_tokens == 15
    assert [m.role for m in result.messages] == ["system", "user", "assistant"]


def test_run_two_round_tool_loop_message_sequence():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
    }
    client = _client_with_queue([
        _response(None, tool_calls=[tool_call]),
        _response("the answer is 5"),
    ])

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    agent = LiteAgent(client=client, model="test-model", tools=[add])
    result = agent.run("what is 2+3?")

    assert result.text == "the answer is 5"
    assert result.iterations == 2
    assert result.usage.total_tokens == 30
    roles = [m.role for m in result.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assistant_msg = result.messages[1]
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0].name == "add"
    tool_msg = result.messages[2]
    assert tool_msg.tool_call_id == "call_1"
    assert tool_msg.content == "5"
    # The assistant tool_calls serialize back to the OpenAI wire format.
    wire = assistant_msg.to_openai()
    assert wire["tool_calls"][0]["function"]["arguments"] == '{"a": 2, "b": 3}'


def test_fixed_pseudo_api_exercises_agent_tool_wire_round_trip():
    requests: list[dict] = []
    responses = [
        _response(
            None,
            tool_calls=[
                {
                    "id": "call-add",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 7, "b": 5}'},
                }
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        ),
        _response(
            "The fixed sum is 12.",
            usage={"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
        ),
    ]

    def pseudo_api(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=responses[len(requests) - 1])

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""

        return a + b

    with LiteLLMClient(
        base_url="http://pseudo-api/v1",
        transport=httpx.MockTransport(pseudo_api),
        max_retries=0,
    ) as client:
        agent = LiteAgent(
            client=client,
            model="pseudo-model",
            system_prompt="Use tools for arithmetic.",
            tools=[add],
        )

        result = agent.run("What is 7 + 5?")

    assert len(requests) == 2
    assert requests[0]["model"] == "pseudo-model"
    assert requests[0]["tools"][0]["function"]["name"] == "add"
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "content": "12",
        "tool_call_id": "call-add",
    }
    assert result.text == "The fixed sum is 12."
    assert result.iterations == 2
    assert result.usage.total_tokens == 33


def test_run_stops_at_max_iterations():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "noop", "arguments": "{}"},
    }

    @tool
    def noop() -> str:
        """Do nothing."""
        return "ok"

    client = _client_with_queue([_response(None, tool_calls=[tool_call]) for _ in range(3)])
    agent = LiteAgent(client=client, model="test-model", tools=[noop], max_iterations=3)

    result = agent.run("loop forever")

    assert result.iterations == 3
    assert result.finish_reason == "max_iterations"


def test_run_disables_tools_after_tool_iteration_budget():
    requests: list[dict] = []
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "noop", "arguments": "{}"},
    }
    responses = [
        _response(None, tool_calls=[tool_call]),
        _response(None, tool_calls=[{**tool_call, "id": "call_2"}]),
        _response("final"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=responses[len(requests) - 1])

    @tool
    def noop() -> str:
        """Do nothing."""

        return "ok"

    client = LiteLLMClient(
        base_url="http://testserver/v1", transport=httpx.MockTransport(handler)
    )
    agent = LiteAgent(
        client=client,
        model="test-model",
        tools=[noop],
        max_iterations=4,
        max_tool_iterations=2,
    )

    result = agent.run("use bounded tools")

    assert result.text == "final"
    assert "tools" in requests[0] and "tools" in requests[1]
    assert "tools" not in requests[2]
    assert requests[2]["messages"][-1]["role"] == "user"
    assert "tool-iteration budget is exhausted" in requests[2]["messages"][-1]["content"]


def test_run_raises_budget_exceeded_with_usage():
    client = _client_with_queue([_response("expensive")])
    agent = LiteAgent(client=client, model="test-model", max_total_tokens=10)

    with pytest.raises(BudgetExceededError) as exc_info:
        agent.run("hi")

    assert exc_info.value.usage.total_tokens == 15


def test_run_structured_returns_validated_model():
    class Answer(BaseModel):
        value: int
        note: str

    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_response('{"value": 42, "note": "ok"}'))

    client = LiteLLMClient(base_url="http://testserver/v1", transport=httpx.MockTransport(handler))
    agent = LiteAgent(client=client, model="test-model")

    answer = agent.run_structured("give me a number", Answer)

    assert answer == Answer(value=42, note="ok")
    request_format = seen_bodies[0]["response_format"]
    assert request_format["type"] == "json_schema"
    assert request_format["json_schema"]["name"] == "Answer"
    assert request_format["json_schema"]["schema"] == Answer.model_json_schema()


def test_run_accepts_history():
    client = _client_with_queue([_response("continued")])
    agent = LiteAgent(client=client, model="test-model")
    history = [Message(role="user", content="earlier"), Message(role="assistant", content="ack")]

    result = agent.run("next", history=history)

    assert [m.role for m in result.messages] == ["user", "assistant", "user", "assistant"]
    assert result.text == "continued"


def test_tool_guard_blocks_call_and_feeds_back_verdict():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
    }
    client = _client_with_queue([
        _response(None, tool_calls=[tool_call]),
        _response("blocked, adjusting"),
    ])
    executed: list[tuple[int, int]] = []
    seen_arguments: list[dict] = []

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        executed.append((a, b))
        return a + b

    def guard(call):
        seen_arguments.append(call.arguments)
        return "blocked by policy: write outside allowed paths"

    agent = LiteAgent(client=client, model="test-model", tools=[add], tool_guard=guard)
    result = agent.run("add 2 and 3")

    assert executed == []
    assert seen_arguments == [{"a": 2, "b": 3}]
    assert result.text == "blocked, adjusting"
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert tool_messages[0].content == "blocked by policy: write outside allowed paths"


def test_tool_guard_none_verdict_executes_tool():
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
    }
    client = _client_with_queue([
        _response(None, tool_calls=[tool_call]),
        _response("the answer is 5"),
    ])

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    agent = LiteAgent(
        client=client, model="test-model", tools=[add], tool_guard=lambda call: None
    )
    result = agent.run("add 2 and 3")

    assert result.text == "the answer is 5"
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert tool_messages[0].content == "5"
