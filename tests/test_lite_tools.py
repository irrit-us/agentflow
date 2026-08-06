from __future__ import annotations

from typing import Optional

from agentflow.lite import Tool, ToolCall, ToolRegistry, tool


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@tool(name="shout", description="Uppercase a string.")
def _shout(text: str, times: int = 1) -> str:
    return text.upper() * times


def _stats(total: float, active: bool, label: Optional[str] = None) -> dict:
    return {"total": total, "active": active, "label": label}


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
        "label": {"type": "string"},
    }
    assert item.parameters["required"] == ["total", "active"]


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
