from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceRequest(BaseModel):
    """One shared or exclusive external-resource requirement for a node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    access: Literal["read", "write"] = "read"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource request name must not be blank")
        return value


class ExternalResourceSettings(BaseModel):
    """Concurrency capacity for one process-local external resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_concurrency: int = Field(default=1, ge=1)


@dataclass
class _ResourceState:
    active: int = 0
    writer: bool = False


class ResourceLease:
    """Idempotently releases one atomically acquired resource set."""

    def __init__(
        self,
        coordinator: ExternalResourceCoordinator,
        requests: tuple[ResourceRequest, ...],
    ):
        self._coordinator = coordinator
        self.requests = requests
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._coordinator._release(self.requests)

    def __enter__(self) -> ResourceLease:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class ExternalResourceCoordinator:
    """Atomically leases all external resources required by a graph node.

    Acquisition is non-blocking because the graph scheduler retries candidates
    whenever running work completes. Checking and reserving every requested
    resource under one lock prevents partial acquisition and lock-order
    deadlocks. Read leases may overlap up to the configured capacity; write
    leases are exclusive for the complete node invocation.
    """

    def __init__(
        self,
        settings: dict[
            str, ExternalResourceSettings | dict[str, Any]
        ] | None = None,
        *,
        default_max_concurrency: int,
    ):
        if default_max_concurrency < 1:
            raise ValueError("default_max_concurrency must be at least 1")
        self._settings = {
            name: ExternalResourceSettings.model_validate(value)
            for name, value in (settings or {}).items()
        }
        invalid = sorted(name for name in self._settings if not name.strip())
        if invalid:
            raise ValueError("external resource names must not be blank")
        self._default_max_concurrency = default_max_concurrency
        self._lock = threading.Lock()
        self._states: dict[str, _ResourceState] = {}

    @staticmethod
    def _normalized(
        requests: list[ResourceRequest] | tuple[ResourceRequest, ...],
    ) -> tuple[ResourceRequest, ...]:
        by_name: dict[str, ResourceRequest] = {}
        for request in requests:
            existing = by_name.get(request.name)
            if existing is not None and existing.access != request.access:
                raise ValueError(
                    f"resource '{request.name}' is requested with conflicting access modes"
                )
            by_name[request.name] = request
        return tuple(by_name[name] for name in sorted(by_name))

    def try_acquire(self, requests: list[ResourceRequest]) -> ResourceLease | None:
        normalized = self._normalized(requests)
        with self._lock:
            for request in normalized:
                state = self._states.setdefault(request.name, _ResourceState())
                configured = self._settings.get(request.name)
                limit = (
                    configured.max_concurrency
                    if configured is not None
                    else self._default_max_concurrency
                )
                if state.active >= limit:
                    return None
                if request.access == "read" and state.writer:
                    return None
                if request.access == "write" and state.active:
                    return None
            for request in normalized:
                state = self._states[request.name]
                state.active += 1
                if request.access == "write":
                    state.writer = True
        return ResourceLease(self, normalized)

    def _release(self, requests: tuple[ResourceRequest, ...]) -> None:
        with self._lock:
            for request in requests:
                state = self._states[request.name]
                state.active -= 1
                if request.access == "write":
                    state.writer = False


@dataclass(frozen=True)
class ConcurrencySnapshot:
    capacity: int
    active: int
    queued: int
    peak_active: int
    acquired: int
    total_wait_seconds: float


class SharedConcurrencyBudget:
    """FIFO concurrency budget shared by synchronous request clients."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._condition = threading.Condition()
        self._next_ticket = 0
        self._serving_ticket = 0
        self._active = 0
        self._queued = 0
        self._peak_active = 0
        self._acquired = 0
        self._total_wait_seconds = 0.0

    @contextmanager
    def hold(self) -> Iterator[None]:
        started = time.monotonic()
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._queued += 1
            while ticket != self._serving_ticket or self._active >= self.capacity:
                self._condition.wait()
            self._queued -= 1
            self._serving_ticket += 1
            self._active += 1
            self._peak_active = max(self._peak_active, self._active)
            self._acquired += 1
            self._total_wait_seconds += time.monotonic() - started
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def snapshot(self) -> ConcurrencySnapshot:
        with self._condition:
            return ConcurrencySnapshot(
                capacity=self.capacity,
                active=self._active,
                queued=self._queued,
                peak_active=self._peak_active,
                acquired=self._acquired,
                total_wait_seconds=self._total_wait_seconds,
            )
