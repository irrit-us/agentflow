from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from agentflow import Graph, kilo
from agentflow.agents.kilo import KiloAdapter
from agentflow.agents.registry import AdapterRegistry
from agentflow.loader import load_pipeline_from_text
from agentflow.prepared import ExecutionPaths
from agentflow.runners.local import LocalRunner
from agentflow.specs import AgentKind, NodeSpec
from agentflow.traces import create_trace_parser


def _paths(tmp_path: Path) -> ExecutionPaths:
    runtime_dir = tmp_path / ".runtime"
    return ExecutionPaths(
        host_workdir=tmp_path,
        host_runtime_dir=runtime_dir,
        target_workdir=str(tmp_path),
        target_runtime_dir=str(runtime_dir),
        app_root=tmp_path,
    )


def test_kilo_adapter_builds_non_interactive_json_command(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kilo",
            "prompt": "Review",
            "model": "anthropic/claude-sonnet-4-6",
            "extra_args": ["--agent", "code"],
        }
    )

    prepared = KiloAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.command == [
        "kilo",
        "run",
        "--format",
        "json",
        "--auto",
        "--model",
        "anthropic/claude-sonnet-4-6",
        "--agent",
        "code",
        "Review",
    ]
    assert prepared.trace_kind == "kilo"
    assert prepared.runtime_files == {}


def test_kilo_adapter_uses_isolated_provider_and_mcp_config(tmp_path: Path):
    node = NodeSpec.model_validate(
        {
            "id": "review",
            "agent": "kilo",
            "prompt": "Review",
            "model": "test-model",
            "provider": {
                "name": "test-provider",
                "base_url": "https://llm.example.test/v1",
                "api_key_env": "TEST_KILO_API_KEY",
            },
            "env": {"TEST_KILO_API_KEY": "test-secret"},
            "mcps": [
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "mcp-filesystem",
                    "args": ["--root", "/workspace"],
                    "env": {"MODE": "read-only"},
                },
                {
                    "name": "search",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test",
                    "headers": {"Authorization": "Bearer test"},
                },
            ],
        }
    )

    prepared = KiloAdapter().prepare(node, "Review", _paths(tmp_path))

    assert prepared.env["KILO_CONFIG"] == str(tmp_path / ".runtime" / "kilo.json")
    assert "OPENCODE_CONFIG" not in prepared.env
    assert prepared.command[-3:] == ["--model", "test-provider/test-model", "Review"]
    config = json.loads(prepared.runtime_files["kilo.json"])
    assert config["provider"]["test-provider"]["options"] == {
        "baseURL": "https://llm.example.test/v1",
        "apiKey": "{env:TEST_KILO_API_KEY}",
    }
    assert config["mcp"] == {
        "filesystem": {
            "type": "local",
            "command": ["mcp-filesystem", "--root", "/workspace"],
            "environment": {"MODE": "read-only"},
        },
        "search": {
            "type": "remote",
            "url": "https://mcp.example.test",
            "headers": {"Authorization": "Bearer test"},
        },
    }


def test_kilo_is_available_through_registry_and_python_dsl():
    assert isinstance(AdapterRegistry().get(AgentKind.KILO), KiloAdapter)

    with Graph("kilo-smoke") as graph:
        kilo(task_id="review", prompt="Review the repo", model="kilocode/auto")

    rendered = graph.to_json()
    loaded = load_pipeline_from_text(rendered, base_dir=Path(__file__).resolve().parents[1])
    assert loaded.nodes[0].agent == AgentKind.KILO
    assert loaded.nodes[0].model == "kilocode/auto"


def test_kilo_supports_builtin_provider_aliases(tmp_path: Path):
    openai_node = NodeSpec.model_validate(
        {"id": "openai", "agent": "kilo", "prompt": "Test", "provider": "openai"}
    )
    anthropic_node = NodeSpec.model_validate(
        {"id": "anthropic", "agent": "kilo", "prompt": "Test", "provider": "anthropic"}
    )

    openai = KiloAdapter().prepare(openai_node, "Test", _paths(tmp_path))
    anthropic = KiloAdapter().prepare(anthropic_node, "Test", _paths(tmp_path))

    assert openai.env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert anthropic.env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


@pytest.mark.asyncio
async def test_kilo_preparation_execution_and_trace_parsing_work_together(tmp_path: Path):
    fake_kilo = tmp_path / "fake_kilo.py"
    fake_kilo.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            from pathlib import Path
            import sys

            expected = [
                "run", "--format", "json", "--auto",
                "--model", "test-provider/test-model", "Integration prompt",
            ]
            if sys.argv[1:] != expected:
                raise SystemExit(f"unexpected arguments: {sys.argv[1:]!r}")
            config = json.loads(Path(os.environ["KILO_CONFIG"]).read_text(encoding="utf-8"))
            if config["mcp"]["local"]["command"] != ["fake-mcp", "--stdio"]:
                raise SystemExit("Kilo MCP config was not materialized")
            if config["provider"]["test-provider"]["options"]["apiKey"] != "{env:TEST_KILO_API_KEY}":
                raise SystemExit("Kilo provider config was not materialized")
            print(json.dumps({"type": "text", "part": {"type": "text", "text": "kilo integration ok"}}))
            """
        ),
        encoding="utf-8",
    )
    fake_kilo.chmod(0o755)
    node = NodeSpec.model_validate(
        {
            "id": "integration",
            "agent": "kilo",
            "prompt": "Integration prompt",
            "executable": str(fake_kilo),
            "model": "test-model",
            "provider": {
                "name": "test-provider",
                "base_url": "https://llm.example.test/v1",
                "api_key_env": "TEST_KILO_API_KEY",
            },
            "env": {"TEST_KILO_API_KEY": "test-secret"},
            "mcps": [
                {
                    "name": "local",
                    "transport": "stdio",
                    "command": "fake-mcp",
                    "args": ["--stdio"],
                }
            ],
        }
    )
    paths = _paths(tmp_path)
    prepared = KiloAdapter().prepare(node, "Integration prompt", paths)

    async def ignore_output(_stream: str, _line: str) -> None:
        return None

    result = await LocalRunner().execute(node, prepared, paths, ignore_output, lambda: False)
    parser = create_trace_parser(AgentKind.KILO, node.id)
    for line in result.stdout_lines:
        parser.feed(line)

    assert result.exit_code == 0
    assert result.stderr_lines == []
    assert parser.finalize() == "kilo integration ok"
    assert (paths.host_runtime_dir / "kilo.json").is_file()
