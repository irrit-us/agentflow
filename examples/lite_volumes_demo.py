"""Lite volumes demo: read-only RAG knowledge-base mount + named-volume data transfer between containers.

The four ``--mount`` types:
    bind    Host directory mounted into the container (use read_only=True for RAG/knowledge bases)
    volume  Docker named volume (shared data between containers: each container mounts the same volume rw)
    tmpfs   In-memory scratch — note: contents are destroyed with the container and
            cannot persist RAG/KB data or transfer data between containers
            (constructing such a Mount emits a UserWarning)
    npipe   Windows named pipe (Windows container scenarios)

This demo: two DockerExecutors (reader / writer roles) share the named volume
"pipeline-shared" to demonstrate data transfer between containers; the
./knowledge_base directory is bind-mounted read-only.

Prerequisite: Docker Desktop (docker CLI on PATH).
"""

from __future__ import annotations

from pathlib import Path

from agentflow.lite import (
    ContainerConfig,
    DockerExecutor,
    Mount,
    ensure_volume,
)

ROOT = Path(__file__).resolve().parent.parent
SHARED_VOLUME = "pipeline-shared"


def make_executor(*, role: str) -> DockerExecutor:
    return DockerExecutor(
        ContainerConfig(
            image="python:3.12-slim",
            network="none",
            mounts=[
                # RAG: the knowledge base is mounted read-only so containers
                # cannot tamper with host data.
                Mount(
                    type="bind",
                    source=str(ROOT / "knowledge_base"),
                    target="/kb",
                    read_only=True,
                ),
                # Data transfer between containers: reader and writer mount
                # the same named volume (rw).
                Mount(type="volume", source=SHARED_VOLUME, target="/shared"),
            ],
        )
    )


def main() -> None:
    ensure_volume(SHARED_VOLUME)  # idempotent: skipped when the volume already exists
    writer = make_executor(role="writer")
    reader = make_executor(role="reader")
    for executor in (writer, reader):
        if not executor.available():
            raise SystemExit("docker CLI not found on PATH; install Docker Desktop first.")

    # The writer container writes to the shared volume.
    out = writer.run("echo 'findings: 3 issues' > /shared/result.txt && echo written")
    print("[writer]", out.stdout.strip() or out.stderr.strip())

    # The reader container reads the same data back from another container,
    # and lists the read-only knowledge base.
    out = reader.run("cat /shared/result.txt; ls /kb 2>/dev/null || echo 'kb empty (demo)'")
    print("[reader]", out.stdout.strip())


if __name__ == "__main__":
    main()
