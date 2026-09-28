from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentflow.agents.claude import ClaudeAdapter
from agentflow.agents.codex import CodexAdapter
from agentflow.agents.structured import parse_structured_output
from agentflow.prepared import ExecutionPaths
from agentflow.specs import NodeSpec


def prepare(tmp_path, agent, options, **kwargs):
    node = NodeSpec(id="review", agent=agent, prompt="private task", cli_options=options, **kwargs)
    paths = ExecutionPaths(host_workdir=tmp_path, host_runtime_dir=tmp_path / "runtime",
                           target_workdir=str(tmp_path), target_runtime_dir=str(tmp_path / "runtime"), app_root=tmp_path)
    adapter = CodexAdapter() if agent == "codex" else ClaudeAdapter()
    return adapter.prepare(node, node.prompt, paths)


def test_codex_native_options(tmp_path):
    result = prepare(tmp_path, "codex", {"system_prompt": "role", "output_schema": {"type": "object"}, "isolate_config": True})
    assert result.stdin == "role\n\n---\n\nprivate task"
    assert result.command[-1] == "-"
    assert "private task" not in result.command
    assert "--ignore-user-config" in result.command
    assert "--ephemeral" in result.command
    assert "--ignore-rules" not in result.command
    assert "HOME" not in result.env
    assert result.runtime_files["structured-output.json"] == ""
    assert json.loads(result.runtime_files["output-schema.json"]) == {"type": "object"}


def test_codex_explicit_provider_survives_isolation(tmp_path):
    result = prepare(tmp_path, "codex", {"isolate_config": True}, provider={"name": "test", "base_url": "https://example.test", "api_key_env": "TEST_KEY"})
    assert "--profile" not in result.command
    assert any(arg.startswith("model_providers=") for arg in result.command)
    assert not result.runtime_symlinks


@pytest.mark.parametrize("tools", [[], ["Read"]])
def test_codex_rejects_exact_tools(tmp_path, tools):
    with pytest.raises(ValueError, match="tool_names"):
        prepare(tmp_path, "codex", {"tool_names": tools})


@pytest.mark.parametrize("override", ["workspace-write", "danger-full-access"])
def test_codex_cannot_weaken_read_only(tmp_path, override):
    with pytest.raises(ValueError, match="weaken"):
        prepare(tmp_path, "codex", {}, env={"AGENTFLOW_CODEX_SANDBOX_MODE": override})


def test_claude_native_options_and_empty_tools(tmp_path):
    result = prepare(tmp_path, "claude", {"system_prompt": "role", "output_schema": {"type": "object"}, "tool_names": [], "isolate_config": True})
    assert result.stdin == "private task"
    assert "private task" not in result.command
    for flag in ["--tools", "--allowedTools", "--setting-sources"]:
        assert result.command[result.command.index(flag) + 1] == ""
    assert "--bare" not in result.command
    assert result.env['CLAUDE_CODE_SIMPLE'] == '0'
    assert result.env['CLAUDE_CODE_DISABLE_CLAUDE_MDS'] == '1'
    assert result.env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
    for flag in ["--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence", "--json-schema", "--append-system-prompt-file"]:
        assert flag in result.command
    assert "bypassPermissions" not in result.command
    assert "dontAsk" in result.command
    assert result.runtime_files["system-prompt.md"] == "role"
    assert json.loads(result.runtime_files["claude-mcp.json"]) == {"mcpServers": {}}


@pytest.mark.parametrize("tool", ["Bash", "Write", "Edit", "Task", "Agent", "mcp__server__write", "Bash(*)"])
def test_claude_readonly_disallows_unsafe_tools(tmp_path, tool):
    with pytest.raises(ValueError):
        prepare(tmp_path, "claude", {"tool_names": [tool]})


def test_options_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        NodeSpec(id="test", agent="codex", prompt="test", cli_options={"dangerous": True})


def test_legacy_prompt_behavior_preserved(tmp_path):
    for agent in ["codex", "claude"]:
        result = prepare(tmp_path, agent, None)
        assert result.stdin is None
        assert "private task" in result.command


def test_structured_parser_success():
    assert parse_structured_output("codex", ['{"type":"turn.completed"}'], '{"ok": true}') == {"ok": True}
    event = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"ok": True}}
    assert parse_structured_output("claude", [json.dumps(event)]) == {"ok": True}


@pytest.mark.parametrize("events,final", [([], '{}'), ([{"type": "turn.failed"}, {"type": "turn.completed"}], '{}'), ([{"type": "error"}, {"type": "turn.completed"}], '{}'), ([{"type": "turn.completed"}], ''), ([{"type": "turn.completed"}], '[]')])
def test_codex_rejects_incomplete_or_failed(events, final):
    with pytest.raises(ValueError):
        parse_structured_output("codex", [json.dumps(event) for event in events], final)


@pytest.mark.parametrize("terminal", [{"subtype": "error_max_turns", "is_error": True}, {"subtype": "success", "is_error": True}, {"subtype": "success", "is_error": False, "result": '{"ok":true}'}, {"subtype": "success", "is_error": False, "structured_output": []}])
def test_claude_last_terminal_result_is_authoritative(terminal):
    success = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"ok": True}}
    with pytest.raises(ValueError):
        parse_structured_output("claude", [json.dumps(success), json.dumps({"type": "result", **terminal})])


def test_parser_rejects_non_json():
    with pytest.raises(ValueError, match="Malformed"):
        parse_structured_output("claude", ["not json"])


def test_codex_read_network_is_independent_of_write_access(tmp_path):
    result = prepare(tmp_path, 'codex', {'isolate_config': True, 'network_access': True,
                                       'readable_roots': ['/related/source']})
    assert '--sandbox' not in result.command
    assert 'default_permissions="agentflow_native"' in result.command
    config = next(value for value in result.command if value.startswith('permissions='))
    import tomllib
    profile = tomllib.loads(config)['permissions']['agentflow_native']
    assert profile['extends'] == ':read-only'
    assert profile['network']['enabled'] is True
    assert profile['filesystem'] == {'/related/source': 'read'}


def test_codex_writable_profile_keeps_context_readonly(tmp_path):
    result = prepare(tmp_path, 'codex', {'network_access': True, 'readable_roots': ['/related']}, tools='read_write')
    import tomllib
    profile = tomllib.loads(next(a for a in result.command if a.startswith('permissions=')))['permissions']['agentflow_native']
    assert profile['extends'] == ':workspace'
    assert profile['filesystem']['/related'] == 'read'


def test_claude_context_roots_and_network_tools(tmp_path):
    result = prepare(tmp_path, 'claude', {'network_access': True, 'readable_roots': ['/related'],
                                        'tool_names': ['Read', 'WebSearch', 'WebFetch']})
    assert result.command[result.command.index('--add-dir') + 1] == '/related'
    assert result.command[result.command.index('--tools') + 1] == 'Read,WebSearch,WebFetch'
    assert 'Bash' not in result.command[result.command.index('--tools') + 1]


@pytest.mark.parametrize('roots', [['relative'], ['/same', '/same'], ['/bad\0path']])
def test_invalid_context_roots(tmp_path, roots):
    with pytest.raises(ValidationError):
        prepare(tmp_path, 'codex', {'readable_roots': roots})


def test_external_sandbox_uses_docker_boundary(tmp_path):
    result = prepare(tmp_path, 'codex', {'external_sandbox': True, 'isolate_config': True,
                                       'network_access': True, 'readable_roots': ['/context']},
                     target={'kind': 'docker', 'image': 'fixture', 'network_policy': 'bridge',
                             'mounts': [{'source': '/source', 'target': '/context'}]})
    assert '--dangerously-bypass-approvals-and-sandbox' in result.command
    assert '--sandbox' not in result.command
    assert not any(arg.startswith(('permissions=', 'default_permissions=')) for arg in result.command)


@pytest.mark.parametrize('target,options', [
    ({'kind': 'local'}, {}),
    ({'kind': 'docker', 'privileged': True}, {}),
    ({'kind': 'docker', 'mount_docker_daemon': True}, {}),
    ({'kind': 'docker', 'workdir_read_only': False}, {}),
    ({'kind': 'docker'}, {'network_access': True}),
    ({'kind': 'docker', 'network_policy': 'bridge'}, {'network_access': False}),
    ({'kind': 'docker'}, {'readable_roots': ['/unmounted']}),
    ({'kind': 'docker', 'mounts': [{'source': '/source', 'target': '/context', 'read_only': False}]}, {}),
])
def test_external_sandbox_rejects_missing_docker_boundaries(tmp_path, target, options):
    with pytest.raises(ValueError):
        prepare(tmp_path, 'codex', {'external_sandbox': True, **options}, target=target)


def test_codex_output_budget_does_not_emit_unrecognized_config(tmp_path):
    result = prepare(tmp_path, 'codex', {'isolate_config': True},
                     model_settings={'max_output_tokens': 768})
    assert not any('model_max_output_tokens' in value for value in result.command)
    assert 'approximately 768 tokens' in result.stdin
