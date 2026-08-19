from __future__ import annotations

import json

import httpx
import pytest

from agentflow.lite import (
    LiteAgent,
    LiteLLMClient,
    NodeSpec,
    Skill,
    SkillRegistry,
    ToolCall,
    ToolRegistry,
    make_agent_factory,
    mcp_skill,
    tool,
)


def _client() -> LiteLLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            },
        )

    return LiteLLMClient(
        base_url="http://testserver/v1",
        transport=httpx.MockTransport(handler),
    )


@tool
def inspect_record(record_id: str) -> str:
    return f"record:{record_id}"


def test_lite_agent_activates_skill_instructions_and_tools_independently():
    skill = Skill(
        name="records",
        instructions="Verify record provenance before reporting.",
        tools=[inspect_record],
        source="local",
    )

    agent = LiteAgent(
        client=_client(),
        model="m",
        system_prompt="Keep the answer concise.",
        skills=[skill],
    )

    assert agent.system_prompt == (
        "Selected skills:\n\n"
        "Skill `records`:\nVerify record provenance before reporting.\n\n"
        "Keep the answer concise."
    )
    assert agent.registry.get("inspect_record") is inspect_record
    assert agent.run("check").text == "ok"


def test_graph_factory_selects_only_node_skills_and_rejects_missing_registry():
    registry = SkillRegistry(
        [
            Skill(name="records", tools=[inspect_record]),
            Skill(name="writing", instructions="Use short sentences."),
        ]
    )
    factory = make_agent_factory(client=_client(), default_model="m", skills=registry)

    agent = factory(NodeSpec(id="review", prompt="review", skills=["writing"]))

    assert agent.skills.names() == ["writing"]
    assert agent.registry.get("inspect_record") is None
    assert "Use short sentences." in (agent.system_prompt or "")

    with pytest.raises(ValueError, match="unknown skill 'missing'"):
        factory(NodeSpec(id="bad", prompt="bad", skills=["missing"]))
    without_registry = make_agent_factory(client=_client(), default_model="m")
    with pytest.raises(ValueError, match="no skill registry"):
        without_registry(NodeSpec(id="bad", prompt="bad", skills=["records"]))


def test_mcp_provider_is_exposed_as_namespaced_skill_tools():
    calls: list[tuple[str, dict]] = []

    class Provider:
        def list_tools(self):
            return [
                {
                    "name": "lookup",
                    "description": "Look up a record.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"record_id": {"type": "string"}},
                        "required": ["record_id"],
                    },
                }
            ]

        def call_tool(self, name: str, arguments: dict):
            calls.append((name, arguments))
            return {"found": arguments["record_id"]}

    skill = mcp_skill("archive", Provider(), instructions="Use the archive as evidence.")
    registry = SkillRegistry([skill]).tool_registry()

    result = registry.dispatch(
        ToolCall(
            id="1",
            name="archive__lookup",
            arguments={"record_id": "R-7", "_remote_name": "other"},
        )
    )

    assert skill.source == "mcp"
    assert calls == [
        ("lookup", {"record_id": "R-7", "_remote_name": "other"})
    ]
    assert json.loads(result) == {"found": "R-7"}


def test_skill_registry_rejects_duplicate_skills_tools_and_agent_tool_collisions():
    with pytest.raises(ValueError, match="duplicate skill"):
        SkillRegistry([Skill(name="same"), Skill(name="same")])
    with pytest.raises(ValueError, match="duplicate tool 'inspect_record'"):
        SkillRegistry(
            [
                Skill(name="one", tools=[inspect_record]),
                Skill(name="two", tools=[inspect_record]),
            ]
        )

    with pytest.raises(ValueError, match="duplicate tool 'inspect_record'"):
        LiteAgent(
            client=_client(),
            model="m",
            tools=ToolRegistry([inspect_record]),
            skills=[Skill(name="records", tools=[inspect_record])],
        )
