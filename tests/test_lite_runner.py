from __future__ import annotations

import json
import threading
import time

import httpx

from agentflow.lite import (
    ExternalResourceSettings,
    GraphRunner,
    GraphSpec,
    LiteLLMClient,
    NodeRun,
    NodeSpec,
    NodeInput,
    ResourceRequest,
    ToolCall,
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


def test_runner_passes_and_persists_one_structured_node_input_unit():
    received: dict[str, NodeInput] = {}

    class StructuredAgent:
        def __init__(self, node_id: str):
            self.node_id = node_id

        def run_node(self, node_input: NodeInput) -> AgentResult:
            received[self.node_id] = node_input
            return AgentResult(
                text=f"out:{node_input.prompt}",
                messages=[],
                usage=Usage(),
                iterations=1,
            )

    graph = GraphSpec(
        nodes=[
            NodeSpec(id="collect", prompt="collect"),
            NodeSpec(
                id="review",
                prompt="review {{ nodes.collect.text }}",
                depends_on=["collect"],
            ),
        ]
    )
    runner = GraphRunner(graph, lambda spec: StructuredAgent(spec.id))

    nodes = runner.run().snapshot()["nodes"]

    assert received["collect"] == NodeInput(node_id="collect", prompt="collect")
    assert received["review"] == NodeInput(
        node_id="review",
        prompt="review out:collect",
        upstream={"collect": "out:collect"},
    )
    assert nodes["review"].input == received["review"]


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
    assert nodes["audit-link--0001"].input == NodeInput(
        node_id="audit-link--0001",
        prompt="audit L-1",
        upstream={"plan": '{"links":[{"id":"L-1"},{"id":"L-2"}]}'},
        fanout_parent="audit-link",
        fanout_item={"id": "L-1"},
    )
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


def test_resource_readers_overlap_and_writer_is_atomic_for_whole_node():
    lock = threading.Lock()
    readers_ready = threading.Event()
    release_readers = threading.Event()
    readers = 0
    writer = False
    violations: list[str] = []

    class ResourceAgent:
        def __init__(self, access: str):
            self.access = access

        def run(self, prompt: str) -> AgentResult:
            nonlocal readers, writer
            if self.access == "read":
                with lock:
                    readers += 1
                    if writer:
                        violations.append("reader overlapped writer")
                    if readers == 2:
                        readers_ready.set()
                release_readers.wait(timeout=5)
                with lock:
                    readers -= 1
            else:
                with lock:
                    if readers or writer:
                        violations.append("writer overlapped another lease")
                    writer = True
                with lock:
                    writer = False
            return AgentResult(text=prompt, messages=[], usage=Usage(), iterations=1)

    graph = GraphSpec(
        resource_settings={"index": ExternalResourceSettings(max_concurrency=2)},
        nodes=[
            NodeSpec(
                id="read-a",
                prompt="read-a",
                resources=[ResourceRequest(name="index")],
            ),
            NodeSpec(
                id="read-b",
                prompt="read-b",
                resources=[ResourceRequest(name="index")],
            ),
            NodeSpec(
                id="write",
                prompt="write",
                resources=[ResourceRequest(name="index", access="write")],
            ),
        ],
    )
    runner = GraphRunner(
        graph,
        lambda spec: ResourceAgent(spec.resources[0].access),
        max_workers=3,
    )

    thread = runner.run_in_background()
    try:
        assert readers_ready.wait(timeout=5)
    finally:
        release_readers.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert violations == []
    assert runner.is_done() is True


def test_runtime_resource_settings_override_legacy_and_graph_capacities():
    graph = GraphSpec(
        resource_settings={"index": ExternalResourceSettings(max_concurrency=1)},
        nodes=[NodeSpec(id="read", prompt="read", resource="index")],
    )

    runner = GraphRunner(
        graph,
        lambda spec: None,
        resource_limits={"index": 2},
        resource_settings={"index": {"max_concurrency": 3}},
    )

    assert runner.resource_settings["index"].max_concurrency == 3


def test_trigger_modes_evaluate_input_and_output_conditions_independently():
    def runner_for(mode: str) -> GraphRunner:
        graph = GraphSpec(
            nodes=[
                NodeSpec(id="producer", prompt="produce", trigger_mode=mode),
                NodeSpec(id="consumer", prompt="consume", depends_on=["producer"]),
            ]
        )
        return GraphRunner(graph, lambda spec: None)

    input_runner = runner_for("input_ready")
    input_snapshot = input_runner.state.snapshot()["nodes"]
    input_runner.state.set_status("consumer", "processing")
    input_snapshot = input_runner.state.snapshot()["nodes"]
    assert input_runner._trigger_ready(
        input_snapshot["producer"], [], input_snapshot
    ) is True

    output_runner = runner_for("output_idle")
    output_runner.state.set_status("consumer", "processing")
    output_snapshot = output_runner.state.snapshot()["nodes"]
    assert output_runner._trigger_ready(
        output_snapshot["producer"], [], output_snapshot
    ) is False
    assert output_runner.blocked() == [
        {"node_id": "producer", "waiting_on": [], "waiting_for": "output_idle"}
    ]

    combined_runner = runner_for("input_and_output")
    combined_runner.state.set_status("consumer", "processing")
    combined_snapshot = combined_runner.state.snapshot()["nodes"]
    assert combined_runner._trigger_ready(
        combined_snapshot["producer"], [], combined_snapshot
    ) is False


def test_output_idle_and_combined_trigger_modes_run_in_normal_dag_state():
    order: list[str] = []

    class RecordingAgent:
        def run(self, prompt: str) -> AgentResult:
            order.append(prompt)
            return AgentResult(text=prompt, messages=[], usage=Usage(), iterations=1)

    graph = GraphSpec(
        nodes=[
            NodeSpec(id="source", prompt="source", trigger_mode="output_idle"),
            NodeSpec(
                id="review",
                prompt="review",
                depends_on=["source"],
                trigger_mode="input_and_output",
            ),
        ]
    )

    GraphRunner(graph, lambda spec: RecordingAgent(), max_workers=1).run()

    assert order == ["source", "review"]


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


def test_resume_reconciles_partially_persisted_fanout(tmp_path):
    state_path = tmp_path / "interrupted-fanout.json"
    graph = GraphSpec(
        nodes=[
            NodeSpec(id="plan", prompt="plan"),
            NodeSpec(
                id="audit",
                prompt="audit {{ item }}",
                fanout=FanOutSpec(from_="plan"),
            ),
        ]
    )
    factory = make_agent_factory(client=_echo_client(), default_model="m")
    interrupted = GraphRunner(graph, factory, state_path=state_path)
    interrupted.state.set_result(
        "plan",
        AgentResult(text="[1,2,3]", messages=[], usage=Usage(), iterations=1),
    )
    interrupted.state.set_status("plan", "finished")

    parent = graph.nodes[1]
    first_child = parent.model_copy(
        update={
            "id": "audit--0001",
            "prompt": "audit 1",
            "depends_on": ["plan"],
            "fanout": None,
        },
        deep=True,
    )
    interrupted.state.add_node(
        NodeRun(spec=first_child, fanout_parent="audit", fanout_item=1)
    )
    interrupted.state.set_status(
        "audit", "processing", detail="interrupted after one child"
    )

    resumed = GraphRunner(graph, factory, state_path=state_path, resume=True)
    nodes = resumed.run().snapshot()["nodes"]

    children = sorted(
        (nrun for nrun in nodes.values() if nrun.fanout_parent == "audit"),
        key=lambda nrun: nrun.spec.id,
    )
    assert [child.spec.id for child in children] == [
        "audit--0001",
        "audit--0002",
        "audit--0003",
    ]
    assert [child.fanout_item for child in children] == [1, 2, 3]
    aggregate = json.loads(nodes["audit"].result.text)
    assert [entry["item"] for entry in aggregate] == [1, 2, 3]
    assert resumed.is_done() is True


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


def test_runtime_fanout_renders_child_container_mounts_and_env():
    from agentflow.lite import ContainerConfig, Mount

    graph = GraphSpec(
        nodes=[
            NodeSpec(id="plan", prompt="plan"),
            NodeSpec(
                id="poc",
                prompt="poc {{ item.alert_id }}",
                fanout=FanOutSpec(from_="plan", items_path="alerts", item_var="item"),
                container=ContainerConfig(
                    image="img",
                    env={"ALERT_ID": "{{ item.alert_id }}"},
                    mounts=[
                        Mount(
                            type="volume",
                            source="poc-{{ item.alert_id }}",
                            target="/scratch",
                        )
                    ],
                ),
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        if prompt == "plan":
            content = '{"alerts":[{"alert_id":"a"},{"alert_id":"b"}]}'
        else:
            content = "done"
        return httpx.Response(200, json=_payload(content))

    client = LiteLLMClient(
        base_url="http://testserver/v1", transport=httpx.MockTransport(handler)
    )
    runner = GraphRunner(graph, make_agent_factory(client=client, default_model="m"))

    nodes = runner.run().snapshot()["nodes"]

    assert nodes["poc--0001"].spec.container.mounts[0].source == "poc-a"
    assert nodes["poc--0002"].spec.container.mounts[0].source == "poc-b"
    assert nodes["poc--0001"].spec.container.env == {"ALERT_ID": "a"}
    # The parent (unexpanded) spec keeps its template.
    assert "{{ item.alert_id }}" in graph.nodes[1].container.mounts[0].source


def test_factory_wires_max_tool_iterations_and_tool_guard():
    seen: list[str] = []

    def guard_factory(spec):
        def guard(call):
            seen.append(f"{spec.id}:{call.name}")
            return None

        return guard

    factory = make_agent_factory(
        client=_echo_client(), default_model="m", tool_guard_factory=guard_factory
    )

    agent = factory(NodeSpec(id="n", prompt="p", max_tool_iterations=3))

    assert agent.max_tool_iterations == 3
    assert agent.tool_guard is not None
    assert agent.tool_guard(ToolCall(id="c", name="run_command", arguments={})) is None
    assert seen == ["n:run_command"]
    # A factory without a guard factory leaves agents unguarded.
    plain = make_agent_factory(client=_echo_client(), default_model="m")(
        NodeSpec(id="n", prompt="p")
    )
    assert plain.tool_guard is None
    assert plain.max_tool_iterations is None
