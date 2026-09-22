from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agentflow.app import create_app
from agentflow.specs import AgentKind, RunRecord, RunEvent
from agentflow.store import LOG_WINDOW_MAX_BYTES, RunStore
from agentflow.traces import create_trace_parser


@pytest.fixture
def monitor(tmp_path):
    store = RunStore(tmp_path / "runs")
    asyncio.run(store.create_run(RunRecord(id="run", status="completed", pipeline={
        "name": "fixture", "nodes": [{"id": "alpha", "agent": "shell", "prompt": "fixture"}]})))
    return store, TestClient(create_app(store=store))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/api/runs", "/api/runs/validate", "/api/runs/run/cancel", "/api/runs/run/rerun", "/"])
def test_monitor_rejects_all_mutations(monitor, method, path):
    store, client = monitor
    before = {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()}
    response = client.request(method, path, json={"pipeline_path": "anything.py"})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert before == {str(p): p.read_bytes() for p in store.base_dir.rglob("*") if p.is_file()}
    assert len(store.list_runs()) == 1


def test_monitor_reads_runs_events_and_artifacts(monitor):
    store, client = monitor
    asyncio.run(store.write_artifact_text("run", "alpha", "output.txt", "answer"))
    asyncio.run(store.append_event("run", RunEvent(run_id="run", type="run_completed", data={"status": "completed"})))
    assert client.get("/").status_code == 200
    assert client.get("/api/runs").json()[0]["id"] == "run"
    assert client.get("/api/runs/run").json()["status"] == "completed"
    assert client.get("/api/runs/run/artifacts/alpha/output.txt").text == "answer"
    assert len(client.get("/api/runs/run/events").json()) == 1
    assert "run_completed" in client.get("/api/runs/run/stream").text
    assert client.get("/api/health").json()["ok"]
    schema = client.get("/openapi.json").json()
    assert all("post" not in methods for methods in schema["paths"].values())


def test_log_windows_preserve_unicode_blanks_and_cursors_during_append(monitor):
    store, client = monitor
    lines = [f"line {i} 中文 🐈" if i % 9 else "" for i in range(123)]
    asyncio.run(store.write_artifact_text("run", "alpha", "stdout.log", "\n".join(lines) + "\n"))
    url = "/api/runs/run/artifacts/alpha/stdout.log/tail"
    page = client.get(url).json()
    assert page["lines"] == lines[-50:]
    asyncio.run(store.append_artifact_text("run", "alpha", "stdout.log", "new output\n"))
    result = page["lines"]
    while page["has_more"]:
        page = client.get(url, params={"before": page["before"]}).json()
        result = page["lines"] + result
    assert result == lines
    assert client.get(url).json()["lines"][-1] == "new output"
    assert client.get(url, params={"limit": 201}).status_code == 422
    assert client.get(url, params={"before": -1}).status_code == 422
    asyncio.run(store.write_artifact_text("run", "alpha", "stdout.log", "short"))
    assert client.get(url).json()["lines"] == ["short"]


def test_artifact_reads_do_not_create_directories_or_escape_store(monitor, tmp_path):
    store, client = monitor
    assert client.get("/api/runs/missing/artifacts/absent/stdout.log/tail").status_code == 404
    assert client.get("/api/runs/missing/artifacts/absent/stdout.log").status_code == 404
    assert not (store.base_dir / "missing").exists()
    assert client.get("/api/runs/run/artifacts/%2E%2E/run.json").status_code == 400
    outside = tmp_path / "secret"
    outside.write_text("secret")
    store.artifact_path("run", "alpha", "stdout.log").symlink_to(outside)
    assert client.get("/api/runs/run/artifacts/alpha/stdout.log/tail").status_code == 400


@pytest.mark.parametrize("agent", [*AgentKind, "custom-agent"])
def test_all_agent_outputs_reach_monitor_artifacts(monitor, agent):
    store, client = monitor
    answer = "answer 中文 <script>safe</script>"
    payloads = {
        "codex": {"type": "response.output_item.done", "item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}]}},
        "claude": {"type": "result", "result": answer},
        "kimi": {"jsonrpc": "2.0", "method": "event", "params": {"type": "ContentPart", "payload": {"type": "text", "text": answer}}},
        "pi": {"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": answer}]}]},
        "opencode": {"type": "message.part.updated", "part": {"type": "text", "text": answer, "state": "completed"}},
        "goose": {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}},
        "deepseek": {"type": "final", "text": answer},
        "zcode": {"response": answer},
    }
    payloads["kilo"] = payloads["opencode"]
    raw = json.dumps(payloads[agent], ensure_ascii=False) if agent in payloads else answer
    parser = create_trace_parser(agent, "alpha")
    parser.start_attempt(1)
    events = parser.feed(raw)
    assert parser.finalize() == answer
    events.append(parser.emit("stderr", "stderr", "diagnostic", "diagnostic", source="stderr"))
    for name, content in {"stdout.log": raw + "\n", "stderr.log": "diagnostic\n",
                          "output.txt": parser.finalize(),
                          "trace.jsonl": "".join(e.model_dump_json() + "\n" for e in events)}.items():
        asyncio.run(store.write_artifact_text("run", "alpha", name, content))
    base = "/api/runs/run/artifacts/alpha/"
    assert client.get(base + "output.txt").text == answer
    assert client.get(base + "stdout.log/tail").json()["lines"] == [raw]
    assert client.get(base + "stderr.log/tail").json()["lines"] == ["diagnostic"]
    traces = [json.loads(line) for line in client.get(base + "trace.jsonl/tail").json()["lines"]]
    assert any(event["content"] == answer for event in traces)
    assert all(event["agent"] == agent for event in traces)
    assert traces[-1]["source"] == "stderr"
    parser.start_attempt(2)
    assert parser.finalize() == ""
    retry_events = parser.feed(raw)
    assert parser.finalize() == answer
    assert all(event.attempt == 2 for event in retry_events)


async def test_store_create_run_rejects_invalid_run_id_atomically(tmp_path):
    store = RunStore(tmp_path / "runs")
    record = RunRecord(
        id="../outside",
        pipeline={"name": "p", "nodes": [{"id": "alpha", "agent": "codex", "prompt": "hi"}]},
    )

    with pytest.raises(ValueError, match="path segment"):
        await store.create_run(record)

    assert store.list_runs() == []
    assert not (tmp_path / "outside").exists()

async def test_store_rejects_artifact_write_path_traversal(tmp_path):
    store = RunStore(tmp_path / "runs")
    await store.create_run(
        RunRecord(
            id="run",
            pipeline={"name": "p", "nodes": [{"id": "alpha", "agent": "codex", "prompt": "hi"}]},
        )
    )

    with pytest.raises(ValueError, match="path segment"):
        await store.write_artifact_text("run", "../../outside", "output.txt", "pwned")
    assert not (tmp_path / "outside" / "output.txt").exists()

def test_store_rejects_artifact_read_path_traversal(tmp_path):
    store = RunStore(tmp_path / "runs")
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("outside-runs-secret", encoding="utf-8")

    with pytest.raises(ValueError, match="path segment"):
        store.read_artifact_text("..", "..", "secret.txt")

    with pytest.raises(ValueError, match="path segment"):
        store.read_artifact_text("run", "alpha", "secret\x00.txt")


def test_monitor_observes_an_external_producer(monitor):
    store, client = monitor
    producer = RunStore(store.base_dir)
    asyncio.run(producer.create_run(RunRecord(id="external", status="running", pipeline={
        "name": "external", "nodes": [{"id": "alpha", "agent": "shell", "prompt": "fixture"}]})))
    assert client.get("/api/runs/external").json()["status"] == "running"
    assert len(client.get("/api/runs").json()) == 2
    run = producer.get_run("external")
    run.status = type(run.status)("completed")
    asyncio.run(producer.persist_run("external"))
    asyncio.run(producer.append_event("external", RunEvent(run_id="external", type="run_completed", data={"status": "completed"})))
    assert client.get("/api/runs/external").json()["status"] == "completed"
    assert "run_completed" in client.get("/api/runs/external/stream").text


def test_log_tail_handles_empty_partial_crlf_and_large_lines(monitor):
    store, client = monitor
    path = store.artifact_path("run", "alpha", "stderr.log")
    url = "/api/runs/run/artifacts/alpha/stderr.log/tail"
    path.write_bytes(b"")
    assert client.get(url).json()["lines"] == []
    lines = ["x" * 20000, "中文", "", "unterminated"]
    path.write_bytes("\r\n".join(lines).encode())
    assert client.get(url).json()["lines"] == lines
    path.write_bytes(b"invalid \xff\n")
    assert client.get(url).json()["lines"] == ["invalid \ufffd"]


@pytest.mark.parametrize("text", ["x" * (2 * 1024 * 1024), *["🐈" * 40000 + "x" * n for n in range(4)]])
def test_log_pages_bound_bytes_and_preserve_long_records(monitor, text):
    store, client = monitor
    path = store.artifact_path("run", "alpha", "stdout.log")
    path.write_text(text, encoding="utf-8")
    url = "/api/runs/run/artifacts/alpha/stdout.log/tail"
    pages = []
    cursor = len(text.encode())
    while cursor:
        page = client.get(url, params={"limit": 1, "before": cursor}).json()
        assert page["end"] == cursor
        assert 0 < page["end"] - page["before"] <= LOG_WINDOW_MAX_BYTES
        assert len(page["lines"]) == 1
        assert "\ufffd" not in page["lines"][0]
        assert page["partial_start"] == page["has_more"]
        assert page["partial_end"] == bool(pages)
        pages.append(page)
        cursor = page["before"]
    assert "".join(page["lines"][0] for page in reversed(pages)) == text


def test_log_byte_boundary_keeps_crlf_together(monitor):
    store, client = monitor
    path = store.artifact_path("run", "alpha", "stdout.log")
    path.write_bytes(b"prefix\r\n" + b"x" * (LOG_WINDOW_MAX_BYTES - 1))
    url = "/api/runs/run/artifacts/alpha/stdout.log/tail"
    page = client.get(url).json()
    assert page["lines"] == ["x" * (LOG_WINDOW_MAX_BYTES - 1)]
    assert not page["partial_start"]
    older = client.get(url, params={"before": page["before"]}).json()
    assert older["lines"] == ["prefix"]
    assert not older["partial_end"]
    assert older["before"] == 0


def test_log_line_limit_does_not_mark_complete_record_as_partial(monitor):
    store, client = monitor
    store.artifact_path("run", "alpha", "stdout.log").write_bytes(
        b"x" * (2 * LOG_WINDOW_MAX_BYTES) + b"\nlast\n")
    page = client.get("/api/runs/run/artifacts/alpha/stdout.log/tail?limit=1").json()
    assert page["lines"] == ["last"]
    assert not page["partial_start"]
    assert not page["partial_end"]


def test_log_tail_reads_off_the_event_loop(monitor, monkeypatch):
    store, client = monitor
    original = store.read_artifact_tail
    store.artifact_path("run", "alpha", "stdout.log").write_text("ok", encoding="utf-8")

    def checked_read(*args, **kwargs):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "read_artifact_tail", checked_read)
    assert client.get("/api/runs/run/artifacts/alpha/stdout.log/tail").json()["lines"] == ["ok"]


async def test_api_zcode_output_from_mock_execution(tmp_path):
    from agentflow.agents.base import AgentAdapter
    from agentflow.agents.registry import AdapterRegistry
    from agentflow.orchestrator import Orchestrator
    from agentflow.prepared import PreparedExecution
    from agentflow.runners.registry import RunnerRegistry
    from agentflow.specs import PipelineSpec

    class MockZCode(AgentAdapter):
        def prepare(self, node, prompt, paths):
            return PreparedExecution(command=["python3", "-c", 'import json; print(json.dumps({"response": "zcode complete"}))'],
                                     env={}, cwd=paths.target_workdir, trace_kind="zcode")

    adapters = AdapterRegistry()
    adapters.register(AgentKind.ZCODE, MockZCode())
    store = RunStore(tmp_path / "runs")
    runtime = Orchestrator(store=store, adapters=adapters, runners=RunnerRegistry())
    run = await runtime.submit(PipelineSpec.model_validate({"name": "zcode-regression", "working_dir": str(tmp_path),
        "nodes": [{"id": "alpha", "agent": "zcode", "prompt": "fixture"}]}))
    completed = await runtime.wait(run.id, timeout=5)
    assert completed.status.value == "completed"
    client = TestClient(create_app(store=store))
    assert client.get(f"/api/runs/{run.id}").json()["nodes"]["alpha"]["output"] == "zcode complete"
    page = client.get(f"/api/runs/{run.id}/artifacts/alpha/trace.jsonl/tail").json()
    assert any(json.loads(line)["content"] == "zcode complete" for line in page["lines"])


def test_log_tail_caps_disk_reads(monitor, monkeypatch):
    import io
    from types import SimpleNamespace

    store, _ = monitor
    reads = []

    class BoundedReader(io.BytesIO):
        def read(self, size=-1):
            assert size >= 0
            reads.append(size)
            return super().read(size)

    stream = BoundedReader(b"x" * (2 * 1024 * 1024))
    monkeypatch.setattr(store, "readable_artifact_path", lambda *_: SimpleNamespace(open=lambda _: stream))
    page = store.read_artifact_tail("run", "alpha", "stdout.log", limit=1)
    assert sum(reads) <= LOG_WINDOW_MAX_BYTES + 1
    assert page["partial_start"]
    assert len(page["lines"][0]) == LOG_WINDOW_MAX_BYTES
