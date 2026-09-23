from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def parse_structured_output(
    agent: str, stdout_lines: Iterable[str], final_text: str | None = None,
) -> dict[str, Any]:
    """Read authoritative native completion evidence, never assistant prose.

    Callers must separately verify process exit status and validate their own
    application schema. Errors deliberately omit potentially sensitive output.
    """
    events = []
    for line in stdout_lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise ValueError("Malformed native CLI event stream") from exc
        if not isinstance(event, dict):
            raise ValueError("Native CLI events must be objects")
        events.append(event)
    if str(agent) == "codex":
        if any(event.get("type") in {"turn.failed", "error"} for event in events):
            raise ValueError("Codex reported a failed turn")
        if not events or events[-1].get("type") != "turn.completed":
            raise ValueError("Codex did not complete its final turn")
        try:
            result = json.loads(final_text or "")
        except (ValueError, TypeError) as exc:
            raise ValueError("Codex did not produce valid structured output") from exc
    elif str(agent) in {"claude", "claudecode"}:
        results = [event for event in events if event.get("type") == "result"]
        if not results:
            raise ValueError("Claude did not produce a terminal result")
        terminal = results[-1]
        if terminal.get("is_error") is not False or terminal.get("subtype") != "success":
            raise ValueError("Claude reported an unsuccessful terminal result")
        result = terminal.get("structured_output")
    else:
        raise ValueError("Structured native output is supported only for Codex and Claude")
    if not isinstance(result, dict):
        raise ValueError("Native structured output must be an object")
    return result
