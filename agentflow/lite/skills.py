from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentflow.lite.tools import Tool, ToolRegistry, ToolSharingConfig


class Skill(BaseModel):
    """An independently selectable bundle of instructions and tools."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    instructions: str | None = None
    tools: list[Tool] = Field(default_factory=list)
    source: Literal["local", "mcp", "custom"] = "custom"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("skill name must not be blank")
        return value


class MCPToolDefinition(BaseModel):
    """The MCP tool fields needed to expose a remote tool to a LiteAgent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(alias="inputSchema")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MCP tool name must not be blank")
        return value


class MCPToolProvider(Protocol):
    """Synchronous boundary implemented by an MCP client or a test double."""

    def list_tools(self) -> list[MCPToolDefinition | dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _mcp_handler(
    provider: MCPToolProvider,
    remote_name: str,
) -> Callable[..., Any]:
    def handler(**arguments: Any) -> Any:
        return provider.call_tool(remote_name, arguments)

    return handler


def mcp_skill(
    name: str,
    provider: MCPToolProvider,
    *,
    description: str = "",
    instructions: str | None = None,
    namespace_tools: bool = True,
) -> Skill:
    """Adapt one MCP server/client as an independently selectable skill.

    Discovery happens once while the skill is built. Tool calls remain lazy and
    are delegated to the supplied synchronous provider. Namespacing is enabled
    by default so tools from different MCP skills cannot silently overwrite one
    another.
    """

    tools: list[Tool] = []
    for raw_definition in provider.list_tools():
        definition = MCPToolDefinition.model_validate(raw_definition)
        local_name = (
            f"{name}__{definition.name}" if namespace_tools else definition.name
        )
        tools.append(
            Tool(
                name=local_name,
                description=definition.description,
                parameters=definition.input_schema,
                handler=_mcp_handler(provider, definition.name),
            )
        )
    return Skill(
        name=name,
        description=description,
        instructions=instructions,
        tools=tools,
        source="mcp",
    )


class SkillRegistry:
    """Own skills independently and preserve coordination for their tools."""

    def __init__(
        self,
        skills: list[Skill] | None = None,
        *,
        tool_sharing: ToolSharingConfig | dict[str, Any] | None = None,
    ):
        self._skills: dict[str, Skill] = {}
        all_tools: list[Tool] = []
        tool_owners: dict[str, str] = {}
        for skill in skills or []:
            if skill.name in self._skills:
                raise ValueError(f"duplicate skill '{skill.name}'")
            self._skills[skill.name] = skill
            for item in skill.tools:
                owner = tool_owners.get(item.name)
                if owner is not None:
                    raise ValueError(
                        f"duplicate tool '{item.name}' in skills '{owner}' and '{skill.name}'"
                    )
                tool_owners[item.name] = skill.name
                all_tools.append(item)
        self._tool_registry = ToolRegistry(all_tools, sharing=tool_sharing)

    @classmethod
    def _from_view(
        cls,
        skills: dict[str, Skill],
        tool_registry: ToolRegistry,
    ) -> SkillRegistry:
        registry = cls()
        registry._skills = dict(skills)
        registry._tool_registry = tool_registry
        return registry

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills)

    def subset(self, names: list[str]) -> SkillRegistry:
        selected: dict[str, Skill] = {}
        tool_names: list[str] = []
        for name in names:
            skill = self.get(name)
            if skill is None:
                raise ValueError(f"unknown skill '{name}'")
            if name in selected:
                continue
            selected[name] = skill
            tool_names.extend(item.name for item in skill.tools)
        return self._from_view(selected, self._tool_registry.subset(tool_names))

    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    def instruction_prelude(self) -> str:
        sections = [
            f"Skill `{skill.name}`:\n{skill.instructions.strip()}"
            for skill in self._skills.values()
            if skill.instructions and skill.instructions.strip()
        ]
        if not sections:
            return ""
        return "Selected skills:\n\n" + "\n\n".join(sections)
