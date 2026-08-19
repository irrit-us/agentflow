from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pytest

from agentflow.lite import (
    Tool,
    ToolAccessPolicy,
    ToolCall,
    ToolRegistry,
    ToolSharingConfig,
    tool,
)


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool(name="shout", description="Uppercase a string.")
def _shout(text: str, times: int = 1) -> str:
    return text.upper() * times


def _stats(total: float, active: bool, label: Optional[str] = None) -> dict:
    return {"total": total, "active": active, "label": label}


def _required_nullable(label: Optional[str]) -> Optional[str]:
    return label


def test_tool_decorator_derives_schema_from_annotations():
    assert add.name == "add"
    assert add.description == "Add two integers."
    assert add.parameters == {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }


def test_tool_decorator_with_overrides_and_default_arg_not_required():
    assert _shout.name == "shout"
    assert _shout.description == "Uppercase a string."
    assert _shout.parameters["properties"] == {
        "text": {"type": "string"},
        "times": {"type": "integer"},
    }
    assert _shout.parameters["required"] == ["text"]


def test_tool_from_function_maps_scalar_types_and_optional():
    item = Tool.from_function(_stats)
    assert item.name == "_stats"
    assert item.parameters["properties"] == {
        "total": {"type": "number"},
        "active": {"type": "boolean"},
        "label": {"type": ["string", "null"]},
    }
    assert item.parameters["required"] == ["total", "active"]


def test_tool_schema_keeps_nullable_parameter_required_without_default():
    item = Tool.from_function(_required_nullable)

    assert item.parameters["properties"] == {
        "label": {"type": ["string", "null"]}
    }
    assert item.parameters["required"] == ["label"]
    assert ToolRegistry([item]).dispatch(
        ToolCall(id="nullable", name="_required_nullable", arguments={"label": None})
    ) == "null"


def test_registry_dispatch_returns_json_for_non_string_results():
    registry = ToolRegistry([add, Tool.from_function(_stats, name="stats")])

    assert registry.dispatch(ToolCall(id="1", name="add", arguments={"a": 2, "b": 3})) == "5"
    assert (
        registry.dispatch(ToolCall(id="2", name="stats", arguments={"total": 1.5, "active": True}))
        == '{"total": 1.5, "active": true, "label": null}'
    )


def test_registry_dispatch_reports_handler_errors_as_strings():
    registry = ToolRegistry([add])

    output = registry.dispatch(ToolCall(id="1", name="add", arguments={"a": "x", "b": 3}))

    assert output.startswith("Error: ")


def test_registry_dispatch_unknown_tool():
    registry = ToolRegistry()

    assert registry.dispatch(ToolCall(id="1", name="nope", arguments={})) == "Error: unknown tool 'nope'"
    assert registry.get("nope") is None


def test_registry_rejects_duplicate_tools():
    with pytest.raises(ValueError, match="duplicate tool 'add'"):
        ToolRegistry([add, add])

    registry = ToolRegistry([add])
    with pytest.raises(ValueError, match="duplicate tool 'add'"):
        registry.register(add)


def test_registry_to_openai_tools_structure():
    registry = ToolRegistry.from_tools([add])

    tools = registry.to_openai_tools()

    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add two integers.",
                "parameters": add.parameters,
            },
        }
    ]


def test_sharing_config_rejects_noop_and_unknown_tool_policies():
    with pytest.raises(ValueError, match="access or max_concurrency"):
        ToolAccessPolicy()
    with pytest.raises(ValueError, match="group requires"):
        ToolAccessPolicy(group="index", max_concurrency=1)

    sharing = ToolSharingConfig(
        policies={"missing": ToolAccessPolicy(max_concurrency=1)}
    )
    with pytest.raises(ValueError, match="unknown tools: missing"):
        ToolRegistry([add], sharing=sharing)

    registry = ToolRegistry(
        [add],
        sharing={"policies": {"add": {"max_concurrency": 1}}},
    )
    assert registry.get("add") is add


def test_max_concurrency_is_shared_across_registry_subsets():
    lock = threading.Lock()
    release = threading.Event()
    two_active = threading.Event()
    active = 0
    peak = 0

    @tool
    def expensive(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_active.set()
        release.wait(timeout=5)
        with lock:
            active -= 1
        return value

    registry = ToolRegistry(
        [expensive],
        sharing=ToolSharingConfig(
            policies={"expensive": ToolAccessPolicy(max_concurrency=2)}
        ),
    )
    subsets = [registry.subset(["expensive"]), registry.subset(["expensive"])]

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(
                    subsets[index % 2].dispatch,
                    ToolCall(id=str(index), name="expensive", arguments={"value": index}),
                )
                for index in range(6)
            ]
            assert two_active.wait(timeout=5)
            with lock:
                assert peak == 2
            release.set()
            assert [future.result(timeout=5) for future in futures] == [
                str(index) for index in range(6)
            ]
    finally:
        release.set()

    assert peak == 2


def test_combined_registry_preserves_source_tool_coordination():
    lock = threading.Lock()
    entered = threading.Event()
    overlap = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0

    @tool
    def serialized(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            entered.set()
            if active == 2:
                overlap.set()
        release.wait(timeout=5)
        with lock:
            active -= 1
        return value

    root = ToolRegistry(
        [serialized],
        sharing={"policies": {"serialized": {"max_concurrency": 1}}},
    )
    combined = ToolRegistry.combine(root.subset(["serialized"]))
    second_started = threading.Event()

    def dispatch_second() -> str:
        second_started.set()
        return root.dispatch(
            ToolCall(id="second", name="serialized", arguments={"value": 2})
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                combined.dispatch,
                ToolCall(id="first", name="serialized", arguments={"value": 1}),
            )
            assert entered.wait(timeout=5)
            second = pool.submit(dispatch_second)
            assert second_started.wait(timeout=5)
            assert not overlap.wait(timeout=0.05)
            assert not second.done()
            with lock:
                assert peak == 1
            release.set()
            assert first.result(timeout=5) == "1"
            assert second.result(timeout=5) == "2"
    finally:
        release.set()

    assert peak == 1


def test_read_write_group_allows_parallel_reads_and_excludes_writes():
    state_lock = threading.Lock()
    release_readers = threading.Event()
    release_writer = threading.Event()
    readers_ready = threading.Event()
    writer_entered = threading.Event()
    late_reader_entered = threading.Event()
    readers = 0
    read_calls = 0
    writer = False
    violations: list[str] = []

    @tool
    def read_index() -> str:
        nonlocal readers, read_calls
        with state_lock:
            read_calls += 1
            readers += 1
            if writer:
                violations.append("reader overlapped writer")
            if readers == 2:
                readers_ready.set()
            if read_calls == 3:
                late_reader_entered.set()
        release_readers.wait(timeout=5)
        with state_lock:
            readers -= 1
        return "read"

    @tool
    def write_index() -> str:
        nonlocal writer
        with state_lock:
            if readers or writer:
                violations.append("writer overlapped another access")
            writer = True
        writer_entered.set()
        release_writer.wait(timeout=5)
        with state_lock:
            writer = False
        return "write"

    registry = ToolRegistry(
        [read_index, write_index],
        sharing=ToolSharingConfig(
            policies={
                "read_index": ToolAccessPolicy(group="index", access="read"),
                "write_index": ToolAccessPolicy(group="index", access="write"),
            }
        ),
    )
    readers_registry = registry.subset(["read_index"])
    writer_registry = registry.subset(["write_index"])

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            first_reads = [
                pool.submit(
                    readers_registry.dispatch,
                    ToolCall(id=str(index), name="read_index", arguments={}),
                )
                for index in range(2)
            ]
            assert readers_ready.wait(timeout=5)
            write_future = pool.submit(
                writer_registry.dispatch,
                ToolCall(id="write", name="write_index", arguments={}),
            )
            assert not writer_entered.wait(timeout=0.05)

            release_readers.set()
            assert writer_entered.wait(timeout=5)
            late_read = pool.submit(
                readers_registry.dispatch,
                ToolCall(id="late", name="read_index", arguments={}),
            )
            assert not late_reader_entered.wait(timeout=0.05)

            release_writer.set()
            assert [future.result(timeout=5) for future in first_reads] == ["read", "read"]
            assert write_future.result(timeout=5) == "write"
            assert late_read.result(timeout=5) == "read"
    finally:
        release_readers.set()
        release_writer.set()

    assert violations == []
