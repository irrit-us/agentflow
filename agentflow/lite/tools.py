from __future__ import annotations

import inspect
import json
import threading
import typing
from contextlib import contextmanager
from types import UnionType
from typing import Any, Callable, Iterator, Literal, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ToolAccessPolicy(BaseModel):
    """Concurrency and read/write coordination for one registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str | None = Field(default=None, min_length=1)
    access: Literal["read", "write"] | None = None
    max_concurrency: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_policy(self) -> ToolAccessPolicy:
        if self.access is None and self.max_concurrency is None:
            raise ValueError("a tool access policy must configure access or max_concurrency")
        if self.group is not None and not self.group.strip():
            raise ValueError("group must not be blank")
        if self.group is not None and self.access is None:
            raise ValueError("group requires read or write access")
        return self


class ToolSharingConfig(BaseModel):
    """Policies applied by every registry derived from one root registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policies: dict[str, ToolAccessPolicy] = Field(default_factory=dict)

    @field_validator("policies")
    @classmethod
    def validate_tool_names(
        cls, policies: dict[str, ToolAccessPolicy]
    ) -> dict[str, ToolAccessPolicy]:
        invalid = sorted(name for name in policies if not name.strip())
        if invalid:
            raise ValueError("tool names in sharing policies must not be blank")
        return policies


class _ReadWriteLock:
    """Writer-preferring read/write lock backed by a condition variable."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def acquire_read(self) -> None:
        with self._condition:
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._condition:
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    def acquire_write(self) -> None:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1

    def release_write(self) -> None:
        with self._condition:
            self._writer = False
            self._condition.notify_all()


class _ToolCoordinator:
    def __init__(self, config: ToolSharingConfig | None = None):
        self._policies = dict(config.policies) if config is not None else {}
        self._limits = {
            name: threading.BoundedSemaphore(policy.max_concurrency)
            for name, policy in self._policies.items()
            if policy.max_concurrency is not None
        }
        groups = {
            policy.group or name
            for name, policy in self._policies.items()
            if policy.access is not None
        }
        self._groups = {name: _ReadWriteLock() for name in groups}

    @contextmanager
    def hold(self, tool_name: str) -> Iterator[None]:
        policy = self._policies.get(tool_name)
        if policy is None:
            yield
            return

        semaphore = self._limits.get(tool_name)
        if semaphore is not None:
            semaphore.acquire()
        lock = self._groups.get(policy.group or tool_name)
        acquired_access = False
        try:
            if lock is not None and policy.access == "read":
                lock.acquire_read()
                acquired_access = True
            elif lock is not None and policy.access == "write":
                lock.acquire_write()
                acquired_access = True
            yield
        finally:
            if acquired_access and lock is not None and policy.access == "read":
                lock.release_read()
            elif acquired_access and lock is not None and policy.access == "write":
                lock.release_write()
            if semaphore is not None:
                semaphore.release()


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
    def __init__(
        self,
        tools: list[Tool] | None = None,
        *,
        sharing: ToolSharingConfig | dict[str, Any] | None = None,
    ):
        self._tools: dict[str, Tool] = {}
        for item in tools or []:
            self.register(item)
        config = ToolSharingConfig.model_validate(sharing) if sharing is not None else None
        missing = (
            sorted(set(config.policies) - set(self._tools)) if config is not None else []
        )
        if missing:
            raise ValueError(
                "tool sharing policies reference unknown tools: " + ", ".join(missing)
            )
        self._coordinator = _ToolCoordinator(config)

    @classmethod
    def from_tools(
        cls,
        tools: list[Tool],
        *,
        sharing: ToolSharingConfig | dict[str, Any] | None = None,
    ) -> ToolRegistry:
        return cls(tools, sharing=sharing)

    @classmethod
    def _from_shared_coordinator(
        cls,
        tools: list[Tool],
        coordinator: _ToolCoordinator,
    ) -> ToolRegistry:
        registry = cls(tools)
        registry._coordinator = coordinator
        return registry

    def register(self, tool_: Tool) -> None:
        self._tools[tool_.name] = tool_

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def subset(self, names: list[str]) -> ToolRegistry:
        """Return a registry view that shares synchronization with this registry."""

        selected: list[Tool] = []
        for name in names:
            item = self.get(name)
            if item is None:
                raise ValueError(f"unknown tool '{name}'")
            selected.append(item)
        return self._from_shared_coordinator(selected, self._coordinator)

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
            with self._coordinator.hold(call.name):
                result = target.handler(**call.arguments)
                if isinstance(result, str):
                    return result
                return json.dumps(result)
        except Exception as exc:  # noqa: BLE001 - tool failures are reported, not raised
            return f"Error: {exc}"
