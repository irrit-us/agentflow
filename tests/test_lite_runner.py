from __future__ import annotations

import json
import threading

import httpx

from agentflow.lite import (
    GraphRunner,
    GraphSpec,
    LiteLLMClient,
    NodeSpec,
    make_agent_factory,
)
from agentflow.lite.graph import EdgeSpec


def _payload(content: str) -> dict:
    return {
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }


def _echo_client(handler_extra=None) -> LiteLLMClient:
    """Client whose reply echoes the last user message."""

    def handler(request: httpx.Request) -> httpx.Response:
        if handler_extra is not None:
            override = handler_extra(request)
            if override is not None:
                return override
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        return httpx.Response(200, json=_payload(f"answer:{user}"))

    return LiteLLMClient(base_url="http://testserver/v1", transport=httpx.MockTransport(handler))


def _linear_graph() -> GraphSpec:
    return GraphSpec(
        name="linear",
        nodes=[
            NodeSpec(id="a", prompt="do a"),
            NodeSpec(id="b", prompt="use {{ nodes.a.text }}"),
        ],
        edges=[EdgeSpec(from_="a", to="b")],
    )


def test_linear_pipeline_runs_in_order_with_prompt_resolution():
    client = _echo_client()
    runner = GraphRunner(_linear_graph(), make_agent_factory(client=client, default_model="m"))

    state = runner.run()

    nodes = state.snapshot()["nodes"]
    assert nodes["a"].status == "finished"
    assert nodes["b"].status == "finished"
    assert nodes["a"].result.text == "answer:do a"
    # b's prompt was resolved with a's output before being sent.
    assert nodes["b"].result.text == "answer:use answer:do a"
    # Status transition sequence per node: preparing is initial, then processing -> finished.
    events = state.snapshot()["events"]
    for nid in ("a", "b"):
        seq = [e.status for e in events if e.node_id == nid]
        assert seq == ["processing", "finished"]
    assert nodes["b"].started_at >= nodes["a"].finished_at
    assert runner.is_done() is True


def test_diamond_waits_for_all_parents():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="a", prompt="pa"),
            NodeSpec(id="b", prompt="pb", depends_on=["a"]),
            NodeSpec(id="c", prompt="pc", depends_on=["a"]),
            NodeSpec(id="d", prompt="pd {{ nodes.b.text }} {{ nodes.c.text }}", depends_on=["b", "c"]),
        ]
    )
    client = _echo_client()
    runner = GraphRunner(graph, make_agent_factory(client=client, default_model="m"))

    state = runner.run()
    nodes = state.snapshot()["nodes"]

    assert all(n.status == "finished" for n in nodes.values())
    assert nodes["d"].result.text == "answer:pd answer:pb answer:pc"
    assert nodes["d"].started_at >= nodes["b"].finished_at
    assert nodes["d"].started_at >= nodes["c"].finished_at


def test_blocked_reports_waiting_on_before_and_during_run():
    # Before run: b is blocked on a; root a is not blocked.
    runner = GraphRunner(_linear_graph(), make_agent_factory(client=_echo_client(), default_model="m"))
    assert runner.blocked() == [{"node_id": "b", "waiting_on": ["a"]}]

    # During run: while a is still processing, b reports waiting_on=["a"].
    gate = threading.Event()

    def slow(request: httpx.Request):
        gate.wait(timeout=5)
        return None  # fall through to echo handler

    runner2 = GraphRunner(
        _linear_graph(),
        make_agent_factory(client=_echo_client(slow), default_model="m"),
    )
    thread = runner2.run_in_background()
    try:
        # Wait until a is actually processing.
        for _ in range(100):
            if runner2.state.snapshot()["nodes"]["a"].status == "processing":
                break
            threading.Event().wait(0.01)
        assert runner2.blocked() == [{"node_id": "b", "waiting_on": ["a"]}]
        assert runner2.is_done() is False
    finally:
        gate.set()
        thread.join(timeout=10)
    assert runner2.is_done() is True


def test_node_error_marks_downstream_errored_without_executing():
    requests = 0
    factory_calls = 0

    def failing(request: httpx.Request):
        nonlocal requests
        requests += 1
        return httpx.Response(400, json={"error": {"message": "boom"}})

    client = LiteLLMClient(
        base_url="http://testserver/v1", transport=httpx.MockTransport(failing)
    )
    base_factory = make_agent_factory(client=client, default_model="m")

    def counting_factory(spec):
        nonlocal factory_calls
        factory_calls += 1
        return base_factory(spec)

    runner = GraphRunner(_linear_graph(), counting_factory)
    state = runner.run()
    nodes = state.snapshot()["nodes"]

    assert nodes["a"].status == "errored"
    assert "boom" in (nodes["a"].error or "")
    assert nodes["b"].status == "errored"
    assert nodes["b"].error == "upstream failed: a"
    assert nodes["b"].started_at is None  # never executed
    assert factory_calls == 1  # b's agent was never constructed
    assert requests == 1  # no retry on 400, and b never called the LLM


def test_run_in_background_and_is_done():
    runner = GraphRunner(_linear_graph(), make_agent_factory(client=_echo_client(), default_model="m"))

    thread = runner.run_in_background()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert runner.is_done() is True
    returned = runner.state
    assert returned.snapshot()["nodes"]["b"].status == "finished"


def test_make_agent_factory_requires_exactly_one_backend():
    import pytest

    with pytest.raises(ValueError):
        make_agent_factory()
    with pytest.raises(ValueError):
        make_agent_factory(client=_echo_client(), router=object())


def test_factory_applies_node_overrides_and_tool_subset():
    from agentflow.lite import Tool, ToolRegistry

    def noop() -> str:
        return "ok"

    registry = ToolRegistry([
        Tool(name="t1", description="", parameters={"type": "object", "properties": {}}, handler=noop),
        Tool(name="t2", description="", parameters={"type": "object", "properties": {}}, handler=noop),
    ])
    factory = make_agent_factory(client=_echo_client(), default_model="m", registry=registry)
    spec = NodeSpec(
        id="x",
        prompt="p",
        system_prompt="sys",
        model="other-model",
        tools=["t2"],
        max_iterations=3,
        max_total_tokens=100,
    )

    agent = factory(spec)

    assert agent.model == "other-model"
    assert agent.system_prompt == "sys"
    assert agent.max_iterations == 3
    assert agent.max_total_tokens == 100
    assert agent.registry.get("t2") is not None
    assert agent.registry.get("t1") is None

    import pytest

    with pytest.raises(ValueError, match="unknown tool"):
        factory(NodeSpec(id="y", prompt="p", tools=["nope"]))
