from __future__ import annotations

from typing import ClassVar

from agentflow.agents.opencode import OpenCodeAdapter


class KiloAdapter(OpenCodeAdapter):
    """Adapter for the Kilo Code CLI (https://kilo.ai).

    Kilo's CLI is OpenCode-compatible, including its non-interactive JSON event
    stream and provider/MCP configuration schema. Runtime configuration remains
    isolated in ``kilo.json`` and is selected with Kilo's trusted
    ``KILO_CONFIG`` override.
    """

    executable_name: ClassVar[str] = "kilo"
    config_filename: ClassVar[str] = "kilo.json"
    config_env_var: ClassVar[str] = "KILO_CONFIG"
    trace_kind: ClassVar[str] = "kilo"
