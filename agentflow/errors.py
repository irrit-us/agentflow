"""Standardized error capture for node executions.

AgentFlow normalizes failures into a small structured shape so every node
failure carries the same fields regardless of the underlying runner or agent:

- ``kind``      -- e.g. ``ValueError``, ``exit_code``, ``tool_error``
- ``message``   -- the human-readable failure message
- ``traceback`` -- full Python traceback when one is available

``StderrClassifier`` additionally lets the orchestrator turn raw stderr lines
into ``error`` trace events while a Python traceback is being emitted, so
failures surface in the trace stream instead of only in the raw log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TRACEBACK_HEADER = "Traceback (most recent call last):"


@dataclass(slots=True)
class CapturedError:
    kind: str
    message: str
    traceback: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"kind": self.kind, "message": self.message, "traceback": self.traceback}


def _parse_exception_line(line: str) -> tuple[str, str]:
    """Split a final traceback line into ``(kind, message)``.

    The last line of a Python traceback is ``ExceptionType: message`` (or just
    ``ExceptionType`` when the exception was raised without a message).
    """
    stripped = line.strip()
    if ":" in stripped:
        kind, message = stripped.split(":", 1)
        return kind.strip() or "Error", message.strip()
    return stripped or "Error", ""


def parse_python_traceback(stderr_lines: list[str]) -> CapturedError | None:
    """Extract the last Python traceback block from raw stderr lines."""
    header_indexes = [
        index
        for index, line in enumerate(stderr_lines)
        if line.rstrip() == _TRACEBACK_HEADER
    ]
    if not header_indexes:
        return None

    start = header_indexes[-1]
    block = [line.rstrip("\n") for line in stderr_lines[start:]]
    exception_index = -1
    for index, line in enumerate(block[1:], start=1):
        if line.strip() and not line.startswith(" "):
            exception_index = index
            break
    if exception_index < 0:
        return None

    kind, message = _parse_exception_line(block[exception_index])
    traceback = "\n".join(block).rstrip()
    if not message:
        # Exceptions without a message may print the value on the next line
        # (e.g. ``raise KeyError`` prints ``KeyError`` then ``'key'``).
        trailing = block[exception_index + 1 :]
        if trailing and trailing[0].strip() and trailing[0].startswith(" "):
            message = trailing[0].strip()
            traceback = "\n".join(block[: exception_index + 2]).rstrip()
    return CapturedError(kind=kind, message=message, traceback=traceback)


def capture_execution_error(
    *,
    stderr_lines: list[str],
    parser_error: dict[str, str | None] | None = None,
    exit_code: int,
) -> CapturedError | None:
    """Resolve the structured error for one execution attempt.

    Errors describe why an attempt failed, so a successful process (exit code
    ``0``) never yields an error. Precedence for failed attempts:

    1. A structured error captured by the trace parser (e.g. the lite engine
       reports ``tool_error``/``error`` events with kind/message/traceback).
    2. A Python traceback parsed from the raw stderr lines.
    3. The last stderr line (or the exit code itself).
    """
    if exit_code == 0:
        return None

    if parser_error:
        kind = str(parser_error.get("kind") or "Error").strip() or "Error"
        message = str(parser_error.get("message") or "").strip()
        traceback = parser_error.get("traceback")
        return CapturedError(kind=kind, message=message, traceback=traceback)

    traceback_error = parse_python_traceback(stderr_lines)
    if traceback_error is not None:
        return traceback_error

    last_stderr = ""
    for line in reversed(stderr_lines):
        if line.strip():
            last_stderr = line.strip()
            break
    if last_stderr:
        return CapturedError(kind="exit_code", message=last_stderr)
    return CapturedError(kind="exit_code", message=f"process exited with code {exit_code}")


class StderrClassifier:
    """Classify raw stderr lines so tracebacks surface as structured errors.

    The classifier tracks whether the current line is inside a Python
    traceback block: the header and the final exception line become ``error``
    events, while intermediate frames stay ``stderr`` events.
    """

    def __init__(self) -> None:
        self._in_traceback = False

    def reset(self) -> None:
        self._in_traceback = False

    def classify(self, line: str) -> tuple[str, str]:
        """Return ``(kind, title)`` for a stderr line."""
        stripped = line.rstrip()
        if not stripped:
            return "stderr", "stderr"

        if stripped == _TRACEBACK_HEADER:
            self._in_traceback = True
            return "error", "Python traceback"

        if self._in_traceback:
            if not stripped.startswith(" ") and not stripped.startswith("\t"):
                self._in_traceback = False
                return "error", "Python error"
            return "stderr", "Traceback frame"

        if re.match(r"^(?:[A-Za-z_][A-Za-z0-9_.]*Error|Exception)\b", stripped):
            return "error", "Python error"
        return "stderr", "stderr"
