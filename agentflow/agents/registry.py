from __future__ import annotations

from agentflow.agents.base import AgentAdapter
from agentflow.agents.claude import ClaudeAdapter
from agentflow.agents.codex import CodexAdapter
from agentflow.agents.deepseek import DeepSeekAdapter
from agentflow.agents.goose import GooseAdapter
from agentflow.agents.kilo import KiloAdapter
from agentflow.agents.kimi import KimiAdapter
from agentflow.agents.opencode import OpenCodeAdapter
from agentflow.agents.pi import PiAdapter
from agentflow.agents.terminus import TerminusAdapter
from agentflow.agents.util import CommandAdapter, PythonAdapter, ShellAdapter, SyncAdapter
from agentflow.agents.zcode import ZCodeAdapter
from agentflow.specs import AgentKind


class AdapterRegistry:
    def __init__(self) -> None:
        self._registry: dict[AgentKind, AgentAdapter] = {
            AgentKind.CODEX: CodexAdapter(),
            AgentKind.DEEPSEEK: DeepSeekAdapter(),
            AgentKind.ZCODE: ZCodeAdapter(),
            AgentKind.CLAUDE: ClaudeAdapter(),
            AgentKind.KIMI: KimiAdapter(),
            AgentKind.PI: PiAdapter(),
            AgentKind.OPENCODE: OpenCodeAdapter(),
            AgentKind.KILO: KiloAdapter(),
            AgentKind.GOOSE: GooseAdapter(),
            AgentKind.TERMINUS: TerminusAdapter(),
            AgentKind.PYTHON: PythonAdapter(),
            AgentKind.SHELL: ShellAdapter(),
            AgentKind.COMMAND: CommandAdapter(),
            AgentKind.SYNC: SyncAdapter(),
        }

    def register(self, kind: AgentKind | str, adapter: AgentAdapter) -> None:
        self._registry[kind] = adapter

    def get(self, kind: AgentKind | str) -> AgentAdapter:
        return self._registry[kind]

    def contains(self, kind: AgentKind | str) -> bool:
        return kind in self._registry


default_adapter_registry = AdapterRegistry()
