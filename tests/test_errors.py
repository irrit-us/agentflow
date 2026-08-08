from __future__ import annotations

from pathlib import Path

import pytest

from agentflow.errors import (
    CapturedError,
    StderrClassifier,
    capture_execution_error,
    parse_python_traceback,
)
from agentflow.orchestrator import Orchestrator
from agentflow.runners.registry import RunnerRegistry
from agentflow.specs import NodeStatus, PipelineSpec
from agentflow.store import RunStore

TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "/tmp/x.py", line 3, in <module>',
    "    raise ValueError('boom')",
    "ValueError: boom",
]


def test_parse_python_traceback_extracts_kind_message_and_traceback():
    error = parse_python_traceback(TRACEBACK)
    assert error == CapturedError(kind="ValueError", message="boom", traceback="\n".join(TRACEBACK))


def test_parse_python_traceback_returns_none_without_traceback():
    assert parse_python_traceback(["boom"]) is None


def test_parse_python_traceback_uses_last_traceback():
    lines = [*TRACEBACK, "ignored", "Traceback (most recent call last):", '  File "/a.py", line 1, in <module>', "    x = 1 / 0", "ZeroDivisionError: division by zero"]
    error = parse_python_traceback(lines)
    assert error is not None
    assert error.kind == "ZeroDivisionError"
    assert error.message == "division by zero"
    assert "ZeroDivisionError" in error.traceback


def test_parse_python_traceback_exception_without_message():
    lines = [
        "Traceback (most recent call last):",
        '  File "/a.py", line 1, in <module>',
        "    raise KeyError('missing')",
        "KeyError: 'missing'",
    ]
    error = parse_python_traceback(lines)
    assert error is not None
    assert error.kind == "KeyError"
    assert error.message == "'missing'"


def test_capture_execution_error_parser_error_wins():
    error = capture_execution_error(
        stderr_lines=TRACEBACK,
        parser_error={"kind": "ZeroDivisionError", "message": "division by zero", "traceback": "tb"},
        exit_code=1,
    )
    assert error == CapturedError(kind="ZeroDivisionError", message="division by zero", traceback="tb")


def test_capture_execution_error_falls_back_to_traceback():
    error = capture_execution_error(stderr_lines=TRACEBACK, parser_error=None, exit_code=1)
    assert error is not None
    assert error.kind == "ValueError"
    assert error.message == "boom"


def test_capture_execution_error_falls_back_to_last_stderr_line():
    error = capture_execution_error(stderr_lines=["first", "boom"], parser_error=None, exit_code=3)
    assert error == CapturedError(kind="exit_code", message="boom")


def test_capture_execution_error_falls_back_to_exit_code():
    error = capture_execution_error(stderr_lines=[], parser_error=None, exit_code=2)
    assert error == CapturedError(kind="exit_code", message="process exited with code 2")


def test_capture_execution_error_none_on_success():
    assert capture_execution_error(stderr_lines=[], parser_error=None, exit_code=0) is None


def test_stderr_classifier_flags_traceback_header_and_exception_line():
    classifier = StderrClassifier()
    assert classifier.classify("Traceback (most recent call last):") == ("error", "Python traceback")
    assert classifier.classify('  File "/a.py", line 1, in <module>') == ("stderr", "Traceback frame")
    assert classifier.classify("    raise ValueError('boom')") == ("stderr", "Traceback frame")
    assert classifier.classify("ValueError: boom") == ("error", "Python error")
    assert classifier.classify("plain stderr") == ("stderr", "stderr")


def test_stderr_classifier_reset():
    classifier = StderrClassifier()
    classifier.classify("Traceback (most recent call last):")
    classifier.reset()
    assert classifier.classify("plain stderr") == ("stderr", "stderr")


def test_stderr_classifier_flags_standalone_error_lines():
    classifier = StderrClassifier()
    assert classifier.classify("RuntimeError: something failed") == ("error", "Python error")
    assert classifier.classify("command not found") == ("stderr", "stderr")


def _make_orchestrator(tmp_path: Path) -> Orchestrator:
    return Orchestrator(store=RunStore(tmp_path / "runs"), runners=RunnerRegistry())


@pytest.mark.asyncio
async def test_orchestrator_captures_python_node_traceback(tmp_path: Path):
    orchestrator = _make_orchestrator(tmp_path)
    pipeline = PipelineSpec.model_validate(
        {
            "name": "python-errors",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "boom",
                    "agent": "python",
                    "prompt": "def fail():\n    raise RuntimeError('kaboom')\nfail()",
                }
            ],
        }
    )
    run = await orchestrator.submit(pipeline)
    completed = await orchestrator.wait(run.id, timeout=10)

    node = completed.nodes["boom"]
    assert node.status == NodeStatus.FAILED
    assert node.exit_code != 0
    assert node.error_kind == "RuntimeError"
    assert node.error_message == "kaboom"
    assert node.error_traceback is not None
    assert "Traceback (most recent call last):" in node.error_traceback
    assert any(event.kind == "error" for event in node.trace_events)
    attempt = node.attempts[0]
    assert attempt.error_kind == "RuntimeError"
    assert attempt.error_message == "kaboom"


@pytest.mark.asyncio
async def test_orchestrator_captures_exit_code_error_when_no_traceback(tmp_path: Path):
    orchestrator = _make_orchestrator(tmp_path)
    pipeline = PipelineSpec.model_validate(
        {
            "name": "exit-errors",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "fail",
                    "agent": "shell",
                    "prompt": "echo 'git push rejected' >&2; exit 3",
                }
            ],
        }
    )
    run = await orchestrator.submit(pipeline)
    completed = await orchestrator.wait(run.id, timeout=10)

    node = completed.nodes["fail"]
    assert node.status == NodeStatus.FAILED
    assert node.error_kind == "exit_code"
    assert node.error_message == "git push rejected"


@pytest.mark.asyncio
async def test_orchestrator_clears_error_on_retry_success(tmp_path: Path):
    orchestrator = _make_orchestrator(tmp_path)
    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)
    pipeline = PipelineSpec.model_validate(
        {
            "name": "retry-errors",
            "working_dir": str(tmp_path),
            "nodes": [
                {
                    "id": "flaky",
                    "agent": "python",
                    "retries": 1,
                    "prompt": (
                        "from pathlib import Path\n"
                        "marker = Path('marker.txt')\n"
                        "if not marker.exists():\n"
                        "    marker.write_text('x')\n"
                        "    raise ValueError('first attempt')\n"
                        "print('ok')\n"
                    ),
                }
            ],
        }
    )
    run = await orchestrator.submit(pipeline)
    completed = await orchestrator.wait(run.id, timeout=10)

    node = completed.nodes["flaky"]
    assert node.status == NodeStatus.COMPLETED
    assert node.error_kind is None
    assert node.error_message is None
    assert len(node.attempts) == 2
    assert node.attempts[0].error_kind == "ValueError"
    assert node.attempts[1].error_kind is None
