from __future__ import annotations

import inspect
import json
import typing
from types import UnionType
from typing import Any, Callable, get_origin

from pydantic import BaseModel, ConfigDict

from agentflow.lite.types import ToolCall

_JSON_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _is_optional(annotation: Any) -> bool:
    if get_origin(annotation) in (typing.Union, UnionType):
        return type(None) in typing.get_args(annotation)
    return False


def _schema_type(annotation: Any) -> str:
    if _is_optional(annotation):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _schema_type(args[0])
    if annotation in _JSON_TYPES:
        return _JSON_TYPES[annotation]
    return "string"


class Tool(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        signature = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            annotation = hints.get(param_name, str)
            properties[param_name] = {"type": _schema_type(annotation)}
            if param.default is inspect.Parameter.empty:
                if not _is_optional(annotation):
                    required.append(param_name)
        doc = inspect.getdoc(fn) or ""
        return cls(
            name=name or fn.__name__,
            description=description if description is not None else doc,
            parameters={"type": "object", "properties": properties, "required": required},
            handler=fn,
        )


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Decorator turning a plain function into a :class:`Tool`.

    Supports both ``@tool`` and ``@tool(name=..., description=...)``.
    """

    def wrap(target: Callable[..., Any]) -> Tool:
        return Tool.from_function(target, name=name, description=description)

    if fn is not None:
        return wrap(fn)
    return wrap


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for item in tools or []:
            self.register(item)

    @classmethod
    def from_tools(cls, tools: list[Tool]) -> ToolRegistry:
        return cls(tools)

    def register(self, tool_: Tool) -> None:
        self._tools[tool_.name] = tool_

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": item.name,
                    "description": item.description,
                    "parameters": item.parameters,
                },
            }
            for item in self._tools.values()
        ]

    def dispatch(self, call: ToolCall) -> str:
        target = self._tools.get(call.name)
        if target is None:
            return f"Error: unknown tool '{call.name}'"
        try:
            result = target.handler(**call.arguments)
        except Exception as exc:  # noqa: BLE001 - tool failures are reported, not raised
            return f"Error: {exc}"
        if isinstance(result, str):
            return result
        return json.dumps(result)
