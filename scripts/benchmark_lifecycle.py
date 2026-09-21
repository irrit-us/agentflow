from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentflow.lifecycle import tree_digest, tree_files


def legacy_files(root: Path) -> list[Path]:
    """The authoring traversal before the library extraction (root jobs only)."""
    result = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        parts = path.relative_to(root).parts
        if parts[0] == "jobs":
            continue
        result.append(path)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def legacy_digest(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(
            f"{path.relative_to(root).as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode()
        )
    return digest.hexdigest()


def measure(root: Path, select, digest, times: list[float]) -> dict:
    files = select(root)
    output_digest = digest(root, files)
    original = os.scandir
    scans = []

    def counted(path):
        scans.append(Path(path).relative_to(root).as_posix())
        return original(path)

    with patch("os.scandir", counted):
        select(root)
    return {
        "files": len(files), "digest": output_digest,
        "median_ms": round(statistics.median(times), 3),
        "samples_ms": [round(value, 3) for value in times],
        "directory_scans": len(scans),
        "excluded_directory_scans": sum(path == "jobs" or path.startswith("jobs/") for path in scans),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic, warm-cache traversal comparison; no agents or network.")
    parser.add_argument("--runtime-dirs", type=int, default=200)
    parser.add_argument("--task-files", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runtime_dirs < 0 or min(args.task_files, args.repeats) < 1:
        parser.error("runtime-dirs must be nonnegative; other counts must be positive")
    with tempfile.TemporaryDirectory(prefix="agentflow-benchmark-") as temporary:
        root = Path(temporary)
        (root / "src").mkdir()
        for i in range(args.task_files):
            (root / "src" / f"task-{i}.txt").write_bytes(b"task content\n" * 100)
        for i in range(args.runtime_dirs):
            directory = root / "jobs" / f"trial-{i}" / "agent"
            directory.mkdir(parents=True)
            for j in range(10):
                (directory / f"event-{j}.json").write_bytes(b"runtime event\n" * 100)
        methods = [
            (legacy_files, legacy_digest),
            (lambda path: tree_files(path, exclude_root_dirs=("jobs",)), tree_digest),
        ]
        # Warm both implementations, then alternate which one runs first.
        for select, digest in methods:
            digest(root, select(root))
        samples = [[], []]
        for repeat in range(args.repeats):
            for index in ([0, 1] if repeat % 2 == 0 else [1, 0]):
                select, digest = methods[index]
                started = time.perf_counter()
                digest(root, select(root))
                samples[index].append((time.perf_counter() - started) * 1000)
        legacy = measure(root, *methods[0], samples[0])
        current = measure(root, *methods[1], samples[1])
        assert legacy["digest"] == current["digest"]
        report = {
            "schema_version": 1,
            "python": platform.python_version(), "platform": platform.platform(),
            "task_files": args.task_files, "runtime_files": args.runtime_dirs * 10,
            "repeats": args.repeats,
            "legacy_rglob": legacy, "pruned_walk": current,
            "scope": "Synthetic filesystem microbenchmark, warm cache, alternating order; not an end-to-end or framework benchmark.",
        }
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
