from __future__ import annotations

import json
import threading
import time

import httpx

from agentflow.lite import (
    GraphRunner,
    GraphSpec,
    LiteLLMClient,
    NodeSpec,
    Usage,
    make_agent_factory,
)
from agentflow.lite.agent import AgentResult
from agentflow.lite.graph import EdgeSpec, FanOutSpec


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


def test_factory_adds_container_shell_tool_for_node_container():
    from agentflow.lite import ContainerConfig, Mount, Tool, ToolRegistry

    def noop() -> str:
        return "ok"

    registry = ToolRegistry([
        Tool(name="t1", description="", parameters={"type": "object", "properties": {}}, handler=noop),
    ])
    factory = make_agent_factory(client=_echo_client(), default_model="m", registry=registry)
    spec = NodeSpec(
        id="sandboxed",
        prompt="p",
        tools=["t1"],
        container=ContainerConfig(
            image="semgrep/semgrep:latest",
            mounts=[Mount(type="bind", source="/host/kb", target="/kb", read_only=True)],
        ),
    )

    agent = factory(spec)

    # Named tools and the container shell tool coexist in the registry.
    assert agent.registry.get("t1") is not None
    run_command = agent.registry.get("run_command")
    assert run_command is not None
    assert run_command.parameters["required"] == ["command"]


def test_factory_container_only_no_named_tools():
    from agentflow.lite import ContainerConfig, ToolRegistry

    factory = make_agent_factory(
        client=_echo_client(), default_model="m", registry=ToolRegistry()
    )
    agent = factory(
        NodeSpec(id="c", prompt="p", container=ContainerConfig(image="python:3.12-slim"))
    )

    assert agent.registry.get("run_command") is not None


def test_factory_without_container_has_no_run_command():
    from agentflow.lite import ToolRegistry

    factory = make_agent_factory(
        client=_echo_client(), default_model="m", registry=ToolRegistry()
    )

    agent = factory(NodeSpec(id="plain", prompt="p"))

    assert agent.registry.get("run_command") is None


def test_runtime_fanout_expands_items_and_aggregates_results():
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="plan", prompt="plan"),
            NodeSpec(
                id="audit-link",
                prompt="audit {{ link.id }}",
                fanout=FanOutSpec(from_="plan", items_path="links", item_var="link"),
                resource="llm",
            ),
            NodeSpec(
                id="review",
                prompt="review {{ nodes.audit-link.text }}",
                depends_on=["audit-link"],
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        if prompt == "plan":
            content = '{"links":[{"id":"L-1"},{"id":"L-2"}]}'
        else:
            content = f"answer:{prompt}"
        return httpx.Response(200, json=_payload(content))

    client = LiteLLMClient(
        base_url="http://testserver/v1", transport=httpx.MockTransport(handler)
    )
    runner = GraphRunner(graph, make_agent_factory(client=client, default_model="m"))

    nodes = runner.run().snapshot()["nodes"]

    assert nodes["audit-link"].status == "finished"
    assert nodes["audit-link--0001"].result.text == "answer:audit L-1"
    assert nodes["audit-link--0002"].result.text == "answer:audit L-2"
    aggregate = json.loads(nodes["audit-link"].result.text)
    assert [entry["item"]["id"] for entry in aggregate] == ["L-1", "L-2"]
    assert nodes["audit-link"].result.usage.total_tokens == 10
    assert "answer:audit L-1" in nodes["review"].result.text
    assert nodes["review"].started_at >= nodes["audit-link"].finished_at


def test_resource_limits_bound_each_resource_independently():
    lock = threading.Lock()
    active = {"forge": 0, "llm": 0}
    peaks = {"forge": 0, "llm": 0}
    gate = threading.Barrier(2)

    class RecordingAgent:
        def __init__(self, resource: str):
            self.resource = resource

        def run(self, prompt: str) -> AgentResult:
            with lock:
                active[self.resource] += 1
                peaks[self.resource] = max(peaks[self.resource], active[self.resource])
            if self.resource == "llm":
                gate.wait(timeout=5)
            time.sleep(0.03)
            with lock:
                active[self.resource] -= 1
            return AgentResult(text=prompt, messages=[], usage=Usage(), iterations=1)

    graph = GraphSpec(
        nodes=[
            NodeSpec(id="forge-a", prompt="a", resource="forge"),
            NodeSpec(id="forge-b", prompt="b", resource="forge"),
            NodeSpec(id="llm-a", prompt="c", resource="llm"),
            NodeSpec(id="llm-b", prompt="d", resource="llm"),
        ]
    )
    runner = GraphRunner(
        graph,
        lambda spec: RecordingAgent(spec.resource),
        max_workers=3,
        resource_limits={"forge": 1, "llm": 2},
    )

    runner.run()

    assert peaks == {"forge": 1, "llm": 2}


def test_priority_controls_ready_node_submission():
    order: list[str] = []

    class RecordingAgent:
        def run(self, prompt: str) -> AgentResult:
            order.append(prompt)
            return AgentResult(text=prompt, messages=[], usage=Usage(), iterations=1)

    graph = GraphSpec(
        nodes=[
            NodeSpec(id="low", prompt="low", priority=0),
            NodeSpec(id="high", prompt="high", priority=10),
        ]
    )

    GraphRunner(graph, lambda spec: RecordingAgent(), max_workers=1).run()

    assert order == ["high", "low"]


def test_persisted_run_resumes_without_reexecuting_finished_nodes(tmp_path):
    state_path = tmp_path / "run.json"
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        return httpx.Response(200, json=_payload(f"answer:{prompt}"))

    graph = _linear_graph()
    client = LiteLLMClient(
        base_url="http://testserver/v1", transport=httpx.MockTransport(handler)
    )
    factory = make_agent_factory(client=client, default_model="m")
    first = GraphRunner(graph, factory, state_path=state_path)
    first.run()
    assert requests == 2
    assert state_path.is_file()

    resumed = GraphRunner(graph, factory, state_path=state_path, resume=True)
    resumed.run()

    assert requests == 2
    assert resumed.is_done() is True
    assert resumed.state.snapshot()["nodes"]["a"].attempts == 1


def test_resume_rejects_state_from_a_different_graph(tmp_path):
    state_path = tmp_path / "run.json"
    factory = make_agent_factory(client=_echo_client(), default_model="m")
    GraphRunner(_linear_graph(), factory, state_path=state_path)
    other = GraphSpec(nodes=[NodeSpec(id="other", prompt="other")])

    import pytest

    with pytest.raises(ValueError, match="does not match"):
        GraphRunner(other, factory, state_path=state_path, resume=True)

    with pytest.raises(ValueError, match="requires state_path"):
        GraphRunner(other, factory, resume=True)


def test_resume_requeues_an_interrupted_node(tmp_path):
    state_path = tmp_path / "interrupted.json"
    graph = GraphSpec(nodes=[NodeSpec(id="work", prompt="work")])
    factory = make_agent_factory(client=_echo_client(), default_model="m")
    interrupted = GraphRunner(graph, factory, state_path=state_path)
    interrupted.state.start_attempt("work")
    interrupted.state.set_status("work", "processing")

    resumed = GraphRunner(graph, factory, state_path=state_path, resume=True)
    before = resumed.state.snapshot()["nodes"]["work"]
    assert before.status == "preparing"
    assert before.started_at is None

    after = resumed.run().snapshot()["nodes"]["work"]
    assert after.status == "finished"
    assert after.attempts == 2


def test_node_retries_transient_failure_up_to_max_attempts():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json=_payload("recovered"))

    client = LiteLLMClient(
        base_url="http://testserver/v1",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    graph = GraphSpec(nodes=[NodeSpec(id="retry", prompt="work", max_attempts=2)])
    runner = GraphRunner(graph, make_agent_factory(client=client, default_model="m"))

    node = runner.run().snapshot()["nodes"]["retry"]

    assert node.status == "finished"
    assert node.result.text == "recovered"
    assert node.error is None
    assert node.attempts == 2
    assert calls == 2
