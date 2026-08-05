from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentflow.agents.base import AgentAdapter
from agentflow.agents.registry import AdapterRegistry
from agentflow.orchestrator import Orchestrator
from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.registry import RunnerRegistry
from agentflow.specs import AgentKind, PipelineSpec
from agentflow.store import RunStore
from agentflow.worktree import create_worktree, remove_worktree


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def test_remove_worktree_deletes_worktree_and_branch(tmp_path):
    repo = _make_repo(tmp_path)
    worktree_dir = create_worktree(repo, "node-a", "run1234567890")
    assert worktree_dir.exists()
    branches = _git(repo, "branch", "--list", "agentflow/*").stdout
    assert "agentflow/run12345/node-a" in branches

    remove_worktree(repo, worktree_dir)

    assert not worktree_dir.exists()
    branches = _git(repo, "branch", "--list", "agentflow/*").stdout
    assert "agentflow/run12345/node-a" not in branches


class RaisingAdapter(AgentAdapter):
    def prepare(self, node, prompt: str, paths: ExecutionPaths) -> PreparedExecution:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_execute_node_cleans_up_worktree_when_attempt_raises(tmp_path):
    repo = _make_repo(tmp_path)
    adapters = AdapterRegistry()
    adapters.register(AgentKind.CODEX, RaisingAdapter())
    orchestrator = Orchestrator(store=RunStore(tmp_path / "runs"), adapters=adapters, runners=RunnerRegistry())

    pipeline = PipelineSpec.model_validate(
        {
            "name": "wt-leak",
            "working_dir": str(repo),
            "use_worktree": True,
            "nodes": [{"id": "plan", "agent": "codex", "prompt": "hi"}],
        }
    )
    run = await orchestrator._create_queued_run(pipeline)

    with pytest.raises(RuntimeError, match="boom"):
        await orchestrator._execute_node(run.id, "plan")

    assert not (repo / ".agentflow" / "worktrees" / run.id / "plan").exists()
    branches = _git(repo, "branch", "--list", "agentflow/*").stdout
    assert "agentflow/" not in branches
