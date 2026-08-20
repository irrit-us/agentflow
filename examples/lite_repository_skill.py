from __future__ import annotations

__doc__ = """Bounded, read-only repository tools shared by the lite examples."""

from pathlib import Path

from agentflow.lite import Skill, ToolAccessPolicy, tool

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 128 * 1024
MAX_SEARCH_FILES = 256
MAX_SEARCH_HITS = 50
MAX_PATTERN_LENGTH = 200
MAX_RESULT_LINE_LENGTH = 400


def _repository_path(path: str) -> Path:
    if not path.strip():
        raise ValueError("path must not be blank")
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("path escapes the repository") from exc
    return target


def _read_bounded_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError("path is not a regular file")
    with path.open("rb") as source:
        content = source.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"file is larger than {MAX_FILE_BYTES} bytes")
    return content.decode("utf-8", errors="replace")


@tool
def read_repository_file(path: str) -> str:
    """Read one bounded text file whose resolved path stays in the repository."""

    return _read_bounded_text(_repository_path(path))


@tool
def search_python(pattern: str, path: str = "agentflow/lite") -> str:
    """Search a bounded number of small Python files inside the repository."""

    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"pattern is longer than {MAX_PATTERN_LENGTH} characters")
    base = _repository_path(path)
    if not base.is_dir():
        raise ValueError("search path is not a directory")

    hits: list[str] = []
    scanned = 0
    for file in sorted(base.rglob("*.py")):
        if scanned >= MAX_SEARCH_FILES or len(hits) >= MAX_SEARCH_HITS:
            break
        resolved = file.resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            continue
        try:
            content = _read_bounded_text(resolved)
        except (OSError, ValueError):
            continue
        scanned += 1
        for lineno, line in enumerate(
            content.splitlines(),
            1,
        ):
            if pattern not in line:
                continue
            excerpt = line.strip()[:MAX_RESULT_LINE_LENGTH]
            relative = resolved.relative_to(ROOT).as_posix()
            hits.append(f"{relative}:{lineno}: {excerpt}")
            if len(hits) >= MAX_SEARCH_HITS:
                break
    return "\n".join(hits) or "no matches"


def repository_skill() -> Skill:
    """Return the independently selectable read-only repository skill."""

    return Skill(
        name="repository-read",
        description="Bounded, read-only inspection of this repository.",
        instructions=(
            "Inspect only files inside the repository. Prefer a narrow search, "
            "read only the files needed for evidence, and never infer a file's "
            "contents from its name."
        ),
        tools=[read_repository_file, search_python],
        source="local",
    )


def repository_tool_policies() -> dict[str, ToolAccessPolicy]:
    """Allow bounded parallel reads while coordinating the shared workspace."""

    return {
        read_repository_file.name: ToolAccessPolicy(
            group="repository",
            access="read",
            max_concurrency=4,
        ),
        search_python.name: ToolAccessPolicy(
            group="repository",
            access="read",
            max_concurrency=2,
        ),
    }
