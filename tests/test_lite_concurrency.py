from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from agentflow.lite import (
    ExternalResourceCoordinator,
    ExternalResourceSettings,
    LiteLLMClient,
    Message,
    ResourceRequest,
    SharedConcurrencyBudget,
)


def _payload() -> dict:
    return {
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {},
    }


def test_shared_request_budget_caps_clients_and_records_waiters():
    budget = SharedConcurrencyBudget(2)
    lock = threading.Lock()
    active = 0
    peak = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return httpx.Response(200, json=_payload())

    clients = [
        LiteLLMClient(
            base_url="http://testserver/v1",
            transport=httpx.MockTransport(handler),
            request_budget=budget,
        )
        for _ in range(6)
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda client: client.chat([Message(role="user", content="go")], "test-model"),
                clients,
            )
        )

    assert [result.message.content for result in results] == ["ok"] * 6
    assert peak == 2
    snapshot = budget.snapshot()
    assert snapshot.active == 0
    assert snapshot.queued == 0
    assert snapshot.peak_active == 2
    assert snapshot.acquired == 6
    assert snapshot.total_wait_seconds > 0


def test_retry_backoff_does_not_hold_request_budget(monkeypatch):
    budget = SharedConcurrencyBudget(1)
    calls = 0
    snapshots = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "busy"})
        return httpx.Response(200, json=_payload())

    monkeypatch.setattr(
        "agentflow.lite.client.time.sleep",
        lambda seconds: snapshots.append(budget.snapshot()),
    )
    client = LiteLLMClient(
        base_url="http://testserver/v1",
        max_retries=1,
        transport=httpx.MockTransport(handler),
        request_budget=budget,
    )
    client.chat([Message(role="user", content="go")], "test-model")

    assert calls == 2
    assert snapshots[0].active == 0
    assert budget.snapshot().acquired == 2


def test_external_resources_are_acquired_as_one_atomic_set():
    coordinator = ExternalResourceCoordinator(
        {
            "database": ExternalResourceSettings(max_concurrency=1),
            "device": ExternalResourceSettings(max_concurrency=1),
        },
        default_max_concurrency=4,
    )
    database = coordinator.try_acquire([ResourceRequest(name="database")])
    assert database is not None

    combined = coordinator.try_acquire(
        [ResourceRequest(name="database"), ResourceRequest(name="device")]
    )
    assert combined is None

    # The failed combined request did not partially reserve the free device.
    device = coordinator.try_acquire([ResourceRequest(name="device")])
    assert device is not None
    device.release()
    database.release()


def test_external_resource_write_lease_excludes_reads_and_is_idempotent():
    coordinator = ExternalResourceCoordinator(
        {"index": {"max_concurrency": 2}},
        default_max_concurrency=4,
    )
    first = coordinator.try_acquire([ResourceRequest(name="index")])
    second = coordinator.try_acquire([ResourceRequest(name="index")])
    assert first is not None
    assert second is not None
    assert coordinator.try_acquire(
        [ResourceRequest(name="index", access="write")]
    ) is None

    first.release()
    second.release()
    writer = coordinator.try_acquire(
        [ResourceRequest(name="index", access="write")]
    )
    assert writer is not None
    assert coordinator.try_acquire([ResourceRequest(name="index")]) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        releases = [pool.submit(writer.release) for _ in range(2)]
        for release in releases:
            release.result(timeout=5)
    final_reader = coordinator.try_acquire([ResourceRequest(name="index")])
    assert final_reader is not None
    final_reader.release()
