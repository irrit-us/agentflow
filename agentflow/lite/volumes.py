from __future__ import annotations

import logging
import subprocess
import warnings
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)

_TMPFS_WARNING = (
    "tmpfs mounts are ephemeral: contents are destroyed with the container and "
    "cannot persist RAG/knowledge-base data or transfer data between containers"
)


class Mount(BaseModel):
    """One ``docker --mount`` entry (bind / volume / tmpfs / npipe)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["bind", "volume", "tmpfs", "npipe"]
    source: str | None = None
    target: str
    read_only: bool = False
    bind_propagation: str | None = None
    volume_nocopy: bool | None = None
    tmpfs_size: str | None = None
    tmpfs_mode: str | None = None

    @model_validator(mode="after")
    def _check_type_specific_options(self) -> Mount:
        if self.type == "tmpfs":
            if self.source is not None:
                raise ValueError("tmpfs mounts must not have a source")
        elif self.source is None:
            raise ValueError(f"{self.type} mounts require a source")
        if self.bind_propagation is not None and self.type != "bind":
            raise ValueError("bind_propagation is only valid for bind mounts")
        if self.volume_nocopy is not None and self.type != "volume":
            raise ValueError("volume_nocopy is only valid for volume mounts")
        if self.tmpfs_size is not None and self.type != "tmpfs":
            raise ValueError("tmpfs_size is only valid for tmpfs mounts")
        if self.tmpfs_mode is not None and self.type != "tmpfs":
            raise ValueError("tmpfs_mode is only valid for tmpfs mounts")
        return self

    def model_post_init(self, __context: object, /) -> None:
        if self.type == "tmpfs":
            warnings.warn(_TMPFS_WARNING, UserWarning, stacklevel=2)
            logger.warning("%s", _TMPFS_WARNING)

    def to_docker_mount(self) -> str:
        parts = [f"type={self.type}"]
        if self.source is not None:
            parts.append(f"src={self.source}")
        parts.append(f"dst={self.target}")
        if self.read_only:
            parts.append("readonly")
        if self.bind_propagation is not None:
            parts.append(f"bind-propagation={self.bind_propagation}")
        if self.volume_nocopy:
            parts.append("volume-nocopy")
        if self.tmpfs_size is not None:
            parts.append(f"tmpfs-size={self.tmpfs_size}")
        if self.tmpfs_mode is not None:
            parts.append(f"tmpfs-mode={self.tmpfs_mode}")
        return ",".join(parts)


def ensure_volume(name: str, docker_bin: str = "docker") -> bool:
    """Create the named volume if missing. Returns True when created."""
    from agentflow.lite.container import ContainerError

    try:
        inspected = subprocess.run(
            [docker_bin, "volume", "inspect", name],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise ContainerError(f"failed to invoke '{docker_bin}': {exc}") from exc
    if inspected.returncode == 0:
        return False
    created = subprocess.run(
        [docker_bin, "volume", "create", name],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        detail = (created.stderr or "").strip()
        raise ContainerError(f"failed to create docker volume '{name}': {detail}")
    return True
