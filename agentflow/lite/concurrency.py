from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


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
