from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from agentflow.lite import (
    GraphRunner,
    GraphSpec,
    LiteLLMClient,
    NodeSpec,
    create_app,
    make_agent_factory,
    make_llm_health_probe,
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


def _finished_runner() -> GraphRunner:
    graph = GraphSpec(
        name="svc-demo",
        nodes=[
            NodeSpec(id="a", prompt="do a"),
            NodeSpec(id="b", prompt="use {{ nodes.a.text }}"),
        ],
        edges=[EdgeSpec(from_="a", to="b")],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=_payload("out:" + body["messages"][-1]["content"]))

    client = LiteLLMClient(base_url="http://testserver/v1", transport=httpx.MockTransport(handler))
    runner = GraphRunner(graph, make_agent_factory(client=client, default_model="m"))
    runner.run()
    return runner


def _pending_runner() -> GraphRunner:
    graph = GraphSpec(
        name="svc-pending",
        nodes=[
            NodeSpec(id="a", prompt="do a"),
            NodeSpec(id="b", prompt="after a", depends_on=["a"]),
        ],
    )
    return GraphRunner(graph, _dummy_factory())


def _dummy_factory():
    def factory(spec):  # pragma: no cover - never executed in these tests
        raise AssertionError("should not run")
    return factory


def test_health_with_ok_probe():
    client = TestClient(create_app(_finished_runner(), health_probe=lambda: {"status": "ok", "latency_ms": 7}))

    body = client.get("/api/health").json()

    assert body == {"status": "ok", "llm": {"status": "ok", "latency_ms": 7}}


def test_health_with_failing_probe_and_without_probe():
    def boom():
        raise RuntimeError("probe exploded")

    client = TestClient(create_app(_finished_runner(), health_probe=boom))
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["llm"]["status"] == "error"
    assert "probe exploded" in body["llm"]["error"]

    client2 = TestClient(create_app(_finished_runner()))
    assert client2.get("/api/health").json()["llm"] == {"status": "unknown"}


def test_state_endpoint_structure():
    runner = _finished_runner()
    client = TestClient(create_app(runner))

    body = client.get("/api/state").json()

    assert body["name"] == "svc-demo"
    assert body["done"] is True
    assert body["edges"] == [["a", "b"]]
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["a"]["status"] == "finished"
    assert by_id["a"]["usage"]["total_tokens"] == 5
    assert by_id["a"]["started_at"] is not None
    assert by_id["a"]["error"] is None


def test_blocked_endpoint():
    client = TestClient(create_app(_pending_runner()))

    body = client.get("/api/blocked").json()

    assert body == {"blocked": [{"node_id": "b", "waiting_on": ["a"]}]}


def test_inspect_returns_messages_and_404_for_unknown():
    client = TestClient(create_app(_finished_runner()))

    body = client.get("/api/nodes/a/inspect").json()

    assert body["node_id"] == "a"
    assert body["status"] == "finished"
    assert body["iterations"] == 1
    assert body["usage"]["total_tokens"] == 5
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant"]
    assert body["messages"][0]["content"] == "do a"
    assert body["messages"][1]["content"] == "out:do a"

    pending = TestClient(create_app(_pending_runner()))
    empty = pending.get("/api/nodes/a/inspect").json()
    assert empty["messages"] == []
    assert empty["usage"] is None

    assert client.get("/api/nodes/ghost/inspect").status_code == 404


class TestHealthProbe:
    def test_ok_path(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            assert request.headers.get("Authorization") == "Bearer sk-test"
            return httpx.Response(200, json={"data": []})

        llm_client = LiteLLMClient(
            base_url="http://testserver/v1",
            api_key="sk-test",
            transport=httpx.MockTransport(handler),
        )
        probe = make_llm_health_probe(llm_client)

        result = probe()

        assert result["status"] == "ok"
        assert isinstance(result["latency_ms"], int)

    def test_error_path_http_500_and_connect_error(self):
        llm_client = LiteLLMClient(
            base_url="http://testserver/v1",
            transport=httpx.MockTransport(lambda request: httpx.Response(500, text="down")),
        )
        assert make_llm_health_probe(llm_client)()["status"] == "error"
        assert "500" in make_llm_health_probe(llm_client)()["error"]

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        llm_client2 = LiteLLMClient(
            base_url="http://testserver/v1", transport=httpx.MockTransport(unreachable)
        )
        result = make_llm_health_probe(llm_client2)()
        assert result["status"] == "error"
        assert "connection refused" in result["error"]
