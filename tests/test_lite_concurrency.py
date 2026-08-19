from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from agentflow.lite import LiteLLMClient, Message, SharedConcurrencyBudget


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
