"""Shared container helpers for the paper-architecture example graphs.

Image constants are grouped in two zones:
- publicly pullable images (docker pull works out of the box);
- locally built images (see README: Dockerfiles live outside this repo and
  must be built before the corresponding graphs can run).
"""

from __future__ import annotations

from agentflow.lite import ContainerConfig, DockerExecutor, Mount

# --- Publicly pullable ---
IMG_BASE = "python:3.12-slim"
IMG_SEMGREP = "semgrep/semgrep:latest"
IMG_SLITHER = "trailofbits/eth-security-toolbox:latest"
IMG_FOUNDRY = "ghcr.io/foundry-rs/foundry:latest"
IMG_AFLPP = "aflplusplus/aflplusplus:latest"
IMG_RADARE2 = "radare/radare2:latest"
IMG_PLAYWRIGHT = "mcr.microsoft.com/playwright/python:latest"
IMG_NODE = "node:22-slim"

# --- Locally built (see README) ---
IMG_FIRMWARE = "agentflow-tools/firmware-qemu:latest"  # QEMU + Greenhouse firmware emulation
IMG_RE = "agentflow-tools/re-ghidra:latest"  # Ghidra/IDA decompilation
IMG_CODEQL = "agentflow-tools/codeql:latest"
IMG_TAMARIN = "agentflow-tools/tamarin:latest"


def executor_for(
    image: str,
    *,
    workspace: str | None = None,
    mounts: list[Mount] | None = None,
    network: str = "none",
    timeout: int = 120,
) -> DockerExecutor:
    """Build a DockerExecutor with audit-safe defaults for the given image."""
    return DockerExecutor(
        ContainerConfig(
            image=image,
            workspace=workspace,
            mounts=list(mounts or []),
            network=network,  # type: ignore[arg-type]
            timeout=timeout,
        )
    )


def shared_volume(name: str) -> Mount:
    """Shared named volume (rw) for data transfer between containers."""
    return Mount(type="volume", source=name, target="/shared")


def kb_mount(host_path: str, target: str = "/kb") -> Mount:
    """Read-only bind mount for RAG / knowledge-base directories."""
    return Mount(type="bind", source=host_path, target=target, read_only=True)
