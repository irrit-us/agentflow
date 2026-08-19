from __future__ import annotations

__doc__ = """Safe lite feature demo: YAML DAG, skills, MCP adapter, and monitor UI.

The default run is deterministic, offline, and exits after printing the three
completed nodes. It exercises real graph scheduling and Tool dispatch through
an ``httpx.MockTransport`` without contacting a model endpoint:

    python examples/lite_pipeline_demo.py

Add ``--monitor`` to keep the read-only UI at http://127.0.0.1:8600/. Add
``--live`` to use an explicitly configured OpenAI-compatible endpoint.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from agentflow.lite import (
    GraphRunner,
    LiteLLMClient,
    SharedConcurrencyBudget,
    Skill,
    SkillRegistry,
    ToolAccessPolicy,
    ToolSharingConfig,
    create_app,
    load_graph,
    make_agent_factory,
    make_llm_health_probe,
    mcp_skill,
    tool,
)

if __package__:
    from .lite_repository_skill import (
        repository_skill,
        repository_tool_policies,
    )
else:
    from lite_repository_skill import repository_skill, repository_tool_policies

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH = ROOT / "examples" / "lite_pipeline.yaml"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class DemoGuidanceProvider:
    """In-process MCP provider used to keep the example network-free."""

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "lookup_control",
                "description": "Look up concise defensive guidance for a risk category.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"risk": {"type": "string"}},
                    "required": ["risk"],
                },
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, str]:
        if name != "lookup_control":
            raise ValueError(f"unknown demo MCP tool '{name}'")
        risk = str(arguments.get("risk", "unspecified"))[:100]
        return {
            "risk": risk,
            "guidance": (
                "Require explicit inputs, avoid shell parsing, and keep execution isolated."
            ),
        }


def build_skill_registry(report_store: list[str] | None = None) -> SkillRegistry:
    """Build independent local/MCP skills with shared Tool coordination."""

    reports = report_store if report_store is not None else []

    @tool
    def publish_report(summary: str) -> str:
        """Atomically publish one bounded report summary to the demo memory store."""

        bounded = summary[:2000]
        reports.append(bounded)
        return f"published report {len(reports)} ({len(bounded)} characters)"

    guidance = mcp_skill(
        "security-guidance",
        DemoGuidanceProvider(),
        description="Transport-independent defensive guidance exposed as an MCP skill.",
        instructions=(
            "Use the namespaced lookup Tool for defensive guidance. Treat its output "
            "as reference material and verify findings against repository evidence."
        ),
    )
    reporting = Skill(
        name="report-publishing",
        description="Bounded publication to an in-memory demo destination.",
        instructions=(
            "Draft a concise final report, call publish_report exactly once with it, "
            "then return the report. Do not claim it was written to disk or sent "
            "over a network."
        ),
        tools=[publish_report],
        source="local",
    )
    policies = {
        **repository_tool_policies(),
        "security-guidance__lookup_control": ToolAccessPolicy(
            group="guidance-catalog",
            access="read",
            max_concurrency=2,
        ),
        "publish_report": ToolAccessPolicy(
            group="report-output",
            access="write",
            max_concurrency=1,
        ),
    }
    return SkillRegistry(
        [repository_skill(), guidance, reporting],
        tool_sharing=ToolSharingConfig(policies=policies),
    )


def _chat_response(
    content: str | None,
    *,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    finish_reason = "stop"
    if tool_name is not None:
        message["tool_calls"] = [
            {
                "id": f"demo-{tool_name}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments or {}),
                },
            }
        ]
        finish_reason = "tool_calls"
    return {
        "model": "offline-demo",
        "choices": [
            {"index": 0, "message": message, "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def _offline_model(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    messages = body.get("messages", [])
    system = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    )
    last_role = messages[-1].get("role") if messages else None

    if "Skill `repository-read`" in system:
        response = (
            _chat_response(
                None,
                tool_name="search_python",
                arguments={"pattern": "subprocess", "path": "agentflow/lite"},
            )
            if last_role != "tool"
            else _chat_response(
                "Bounded read-only scan complete. Potential process launches require "
                "review, but the scan alone does not establish exploitability."
            )
        )
    elif "Skill `security-guidance`" in system:
        response = (
            _chat_response(
                None,
                tool_name="security-guidance__lookup_control",
                arguments={"risk": "process launch"},
            )
            if last_role != "tool"
            else _chat_response(
                "The process-launch findings need explicit-input and shell-parsing "
                "checks. No exploitability is established by the bounded scan."
            )
        )
    elif "Skill `report-publishing`" in system:
        response = (
            _chat_response(
                None,
                tool_name="publish_report",
                arguments={
                    "summary": "Demo audit: review process launches; no confirmed exploit."
                },
            )
            if last_role != "tool"
            else _chat_response(
                "Audit report published to the in-memory demo store. No confirmed "
                "exploit was identified."
            )
        )
    else:
        response = _chat_response("offline demo endpoint ready")
    return httpx.Response(200, json=response)


def build_offline_client() -> LiteLLMClient:
    """Return a fast deterministic client that cannot access the network."""

    return LiteLLMClient(
        base_url="http://offline-demo/v1",
        transport=httpx.MockTransport(_offline_model),
        max_retries=0,
        timeout=5,
        request_budget=SharedConcurrencyBudget(capacity=2),
    )


def build_live_client() -> LiteLLMClient:
    """Return a bounded live client after rejecting accidental unauthenticated use."""

    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LITE_API_KEY")
    if base_url == DEFAULT_BASE_URL and not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is required for the default endpoint; configure "
            "OPENAI_BASE_URL for an explicit local endpoint."
        )
    return LiteLLMClient(
        base_url=base_url,
        api_key=api_key,
        timeout=30,
        max_retries=1,
        request_budget=SharedConcurrencyBudget(capacity=2),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="use OPENAI_BASE_URL instead of the deterministic offline model",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="serve the read-only monitor after starting the graph",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=Path(os.environ.get("LITE_GRAPH", DEFAULT_GRAPH)),
        help="graph path (custom graphs require --live)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    graph_path = args.graph.resolve()
    if not args.live and graph_path != DEFAULT_GRAPH.resolve():
        raise SystemExit("custom graphs require --live; the offline model is intentionally fixed")

    graph = load_graph(graph_path)
    reports: list[str] = []
    skills = build_skill_registry(reports)
    client = build_live_client() if args.live else build_offline_client()
    factory = make_agent_factory(
        client=client,
        default_model=(
            os.environ.get("LITE_MODEL", "gpt-4o-mini")
            if args.live
            else "offline-demo"
        ),
        skills=skills,
    )
    runner = GraphRunner(graph, factory, max_workers=4)
    try:
        if args.monitor:
            runner.run_in_background()
            app = create_app(runner, health_probe=make_llm_health_probe(client))
            print("monitor UI: http://127.0.0.1:8600/")
            uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")
            return

        state = runner.run().snapshot()["nodes"]
        for node_id, node_run in state.items():
            upstream = sorted(node_run.input.upstream) if node_run.input is not None else []
            text = node_run.result.text if node_run.result is not None else node_run.error
            print(f"{node_id}: {node_run.status}; NodeInput.upstream={upstream}")
            print(f"  {text}")
        print(f"in-memory reports: {len(reports)}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
