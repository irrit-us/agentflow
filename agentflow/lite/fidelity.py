from __future__ import annotations

import json
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentflow.lite.graph import load_graph


class CapabilityState(str, Enum):
    """How faithfully a paper capability is represented at runtime."""

    NOT_REQUIRED = "not-required"
    PROMPT_ONLY = "prompt-only"
    ENFORCED = "enforced"


class FidelityLevel(str, Enum):
    """Overall implementation fidelity of a paper architecture."""

    STRUCTURAL = "structural"
    PARTIAL = "partial"
    ENFORCED = "enforced"


class RunReadiness(str, Enum):
    """Whether an architecture can be exercised beyond schema validation."""

    RUNNABLE = "runnable"
    MOCK_RUNNABLE = "mock-runnable"
    SPEC_ONLY = "spec-only"


CAPABILITY_NAMES = (
    "real_feedback",
    "deterministic_oracle",
    "human_approval",
    "fail_policy",
    "stateful_target",
    "evidence_contract",
    "sandbox",
)

# Keep this list tied to concrete lite implementations. Adding an "enforced"
# manifest claim requires adding the corresponding runtime adapter first.
RUNTIME_CAPABILITY_ADAPTERS = frozenset({"sandbox"})


class CapabilityFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    real_feedback: CapabilityState
    deterministic_oracle: CapabilityState
    human_approval: CapabilityState
    fail_policy: CapabilityState
    stateful_target: CapabilityState
    evidence_contract: CapabilityState
    sandbox: CapabilityState

    def items(self) -> Iterator[tuple[str, CapabilityState]]:
        for name in CAPABILITY_NAMES:
            yield name, getattr(self, name)


class ArchitectureRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)

    @field_validator("tools", "images", "licenses", "devices")
    @classmethod
    def require_unique_nonempty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("requirement values must not be empty")
        if len(values) != len(set(values)):
            raise ValueError("requirement values must be unique")
        return values


class PaperArchitectureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    graph_name: str = Field(min_length=1)
    graph_path: str = Field(min_length=1)
    fidelity: FidelityLevel
    readiness: RunReadiness
    requirements: ArchitectureRequirements
    capabilities: CapabilityFlags
    non_runnable_reasons: list[str] = Field(default_factory=list)

    @field_validator("graph_path")
    @classmethod
    def require_relative_yaml_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("graph_path must stay relative to the manifest directory")
        if path.suffix not in {".yaml", ".yml"}:
            raise ValueError("graph_path must identify a YAML graph")
        return value

    @field_validator("non_runnable_reasons")
    @classmethod
    def require_unique_reasons(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("non-runnable reasons must not be empty")
        if len(values) != len(set(values)):
            raise ValueError("non-runnable reasons must be unique")
        return values

    @model_validator(mode="after")
    def validate_readiness(self) -> PaperArchitectureEntry:
        if self.readiness is RunReadiness.RUNNABLE and self.non_runnable_reasons:
            raise ValueError("runnable entries cannot have non-runnable reasons")
        if self.readiness is not RunReadiness.RUNNABLE and not self.non_runnable_reasons:
            raise ValueError("non-runnable entries must state exact reasons")
        if self.fidelity is FidelityLevel.ENFORCED:
            prompt_only = [
                name
                for name, state in self.capabilities.items()
                if state is CapabilityState.PROMPT_ONLY
            ]
            if prompt_only:
                raise ValueError(
                    "enforced fidelity cannot contain prompt-only capabilities: "
                    + ", ".join(prompt_only)
                )
        enforced = [
            name
            for name, state in self.capabilities.items()
            if state is CapabilityState.ENFORCED
        ]
        if self.fidelity is FidelityLevel.STRUCTURAL and enforced:
            raise ValueError(
                "structural fidelity cannot contain enforced capabilities: "
                + ", ".join(enforced)
            )
        if self.fidelity is FidelityLevel.PARTIAL and not enforced:
            raise ValueError("partial fidelity requires at least one enforced capability")
        return self


class PaperArchitectureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    entries: list[PaperArchitectureEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_graphs(self) -> PaperArchitectureManifest:
        paths = [entry.graph_path for entry in self.entries]
        names = [entry.graph_name for entry in self.entries]
        duplicate_paths = sorted({path for path in paths if paths.count(path) > 1})
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        if duplicate_paths:
            raise ValueError("duplicate graph paths: " + ", ".join(duplicate_paths))
        if duplicate_names:
            raise ValueError("duplicate graph names: " + ", ".join(duplicate_names))
        return self


def validate_runtime_capability_claims(manifest: PaperArchitectureManifest) -> None:
    """Reject enforced claims that have no concrete lite runtime adapter."""

    for entry in manifest.entries:
        unsupported = [
            name
            for name, state in entry.capabilities.items()
            if state is CapabilityState.ENFORCED and name not in RUNTIME_CAPABILITY_ADAPTERS
        ]
        if unsupported:
            raise ValueError(
                f"{entry.graph_path} claims enforced capabilities without runtime adapters: "
                + ", ".join(unsupported)
            )


def validate_manifest_graphs(
    manifest: PaperArchitectureManifest,
    graph_root: str | Path,
) -> None:
    """Cross-check graph identity, images, and container capability claims."""

    root = Path(graph_root).resolve()
    for entry in manifest.entries:
        graph_path = (root / entry.graph_path).resolve()
        if graph_path.parent != root and root not in graph_path.parents:
            raise ValueError(f"graph path escapes manifest directory: {entry.graph_path}")
        if not graph_path.is_file():
            raise ValueError(f"manifest graph does not exist: {entry.graph_path}")
        graph = load_graph(graph_path)
        if graph.name != entry.graph_name:
            raise ValueError(
                f"{entry.graph_path} declares graph name {graph.name!r}, "
                f"manifest has {entry.graph_name!r}"
            )
        actual_images = sorted(
            {node.container.image for node in graph.nodes if node.container is not None}
        )
        if sorted(entry.requirements.images) != actual_images:
            raise ValueError(
                f"{entry.graph_path} image requirements do not match the graph: "
                f"expected {actual_images!r}, got {sorted(entry.requirements.images)!r}"
            )
        has_containers = bool(actual_images)
        sandbox_state = entry.capabilities.sandbox
        if has_containers and sandbox_state is not CapabilityState.ENFORCED:
            raise ValueError(
                f"{entry.graph_path} has container nodes but does not mark sandbox as enforced"
            )
        if not has_containers and sandbox_state is CapabilityState.ENFORCED:
            raise ValueError(
                f"{entry.graph_path} marks sandbox as enforced without a container node"
            )
        if has_containers and "docker" not in entry.requirements.tools:
            raise ValueError(
                f"{entry.graph_path} uses container images but does not require docker"
            )


def load_paper_architecture_manifest(
    path: str | Path,
    *,
    validate_graphs: bool = True,
) -> PaperArchitectureManifest:
    """Load and validate a paper-architecture fidelity manifest."""

    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    manifest = PaperArchitectureManifest.model_validate(data)
    validate_runtime_capability_claims(manifest)
    if validate_graphs:
        validate_manifest_graphs(manifest, source.parent)
    return manifest
