from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agentflow.lite.client import LiteLLMClient
from agentflow.lite.runner import GraphRunner

_ALLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(runner: GraphRunner, health_probe: Callable[[], dict] | None = None) -> FastAPI:
    app = FastAPI(title=f"lite-monitor:{runner.graph.name}")

    @app.middleware("http")
    async def read_only_guard(request, call_next):
        # Monitor only, read-only by design: reject every non-GET/HEAD/OPTIONS method.
        if request.method not in _ALLOWED_METHODS:
            return JSONResponse({"detail": "monitor API is read-only"}, status_code=405)
        return await call_next(request)

    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health() -> dict:
        if health_probe is None:
            llm: dict = {"status": "unknown"}
        else:
            try:
                llm = health_probe()
            except Exception as exc:  # noqa: BLE001 - probe failures are reported
                llm = {"status": "error", "error": str(exc)}
        return {"status": "ok", "llm": llm}

    @app.api_route("/api/state", methods=["GET", "HEAD"])
    def state() -> dict:
        snapshot = runner.state.snapshot()["nodes"]
        nodes = [
            {
                "id": nid,
                "status": nrun.status,
                "started_at": nrun.started_at,
                "finished_at": nrun.finished_at,
                "error": nrun.error,
                "usage": nrun.result.usage.model_dump() if nrun.result else None,
            }
            for nid, nrun in snapshot.items()
        ]
        return {
            "name": runner.graph.name,
            "done": runner.is_done(),
            "nodes": nodes,
            "edges": [[src, dst] for src, dst in runner.graph.all_edges()],
        }

    @app.api_route("/api/blocked", methods=["GET", "HEAD"])
    def blocked() -> dict:
        return {"blocked": runner.blocked()}

    @app.api_route("/api/nodes/{node_id}/inspect", methods=["GET", "HEAD"])
    def inspect(node_id: str) -> dict:
        nrun = runner.state.snapshot()["nodes"].get(node_id)
        if nrun is None:
            raise HTTPException(status_code=404, detail=f"unknown node '{node_id}'")
        result = nrun.result
        return {
            "node_id": node_id,
            "status": nrun.status,
            "error": nrun.error,
            "iterations": result.iterations if result else None,
            "usage": result.usage.model_dump() if result else None,
            "messages": [m.model_dump() for m in result.messages] if result else [],
        }

    web_dir = Path(__file__).parent / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True))

    return app


def make_llm_health_probe(client: LiteLLMClient, timeout: float = 5.0) -> Callable[[], dict]:
    def probe() -> dict:
        start = time.monotonic()
        try:
            response = client._client.get(
                f"{client.base_url}/models", headers=client._headers(), timeout=timeout
            )
        except httpx.HTTPError as exc:
            return {"status": "error", "error": str(exc)}
        latency_ms = int((time.monotonic() - start) * 1000)
        if response.status_code >= 400:
            return {"status": "error", "error": f"HTTP {response.status_code}"}
        return {"status": "ok", "latency_ms": latency_ms}

    return probe
