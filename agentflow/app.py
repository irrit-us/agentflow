from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentflow.orchestrator import Orchestrator
from agentflow.store import RunStore, _safe_path_segment
from agentflow.specs import RunRecord, RunEvent


def create_app(*, store: RunStore | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    store = store or RunStore(os.getenv("AGENTFLOW_RUNS_DIR", ".agentflow/runs"))
    app = FastAPI(title="AgentFlow", version="0.1.0")
    app.state.store = store

    @app.middleware("http")
    async def read_only(request: Request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "monitor is read-only"}, status_code=405,
                                headers={"Allow": "GET, HEAD"})
        return await call_next(request)

    def snapshot_run(run_id: str) -> RunRecord:
        # Monitor persisted state even when the producer is another CLI process.
        path = app.state.store.base_dir / _safe_path_segment(run_id, "run_id") / "run.json"
        if not path.resolve().is_relative_to(app.state.store.base_dir.resolve()):
            raise ValueError("run path escapes store")
        return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def snapshot_runs() -> list[RunRecord]:
        runs = []
        for path in app.state.store.base_dir.glob("*/run.json"):
            try:
                runs.append(RunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def snapshot_events(run_id: str) -> list[RunEvent]:
        snapshot_run(run_id)
        path = app.state.store.base_dir / run_id / "events.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(RunEvent.model_validate_json(line))
            except ValueError:
                continue  # A producer may still be writing its last record.
        return events

    base_dir = os.path.join(os.path.dirname(__file__), "web")
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))
    app.mount("/static", StaticFiles(directory=os.path.join(base_dir, "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            name="index.html",
            request=request,
            context={},
        )

    @app.get("/api/runs")
    async def list_runs() -> JSONResponse:
        return JSONResponse([run.model_dump(mode="json") for run in snapshot_runs()])

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        try:
            run = snapshot_run(run_id)
        except (KeyError, FileNotFoundError, ValueError) as exc:  # pragma: no cover - exercised by API callers only
            raise HTTPException(status_code=404, detail="run not found") from exc
        return JSONResponse(run.model_dump(mode="json"))

    @app.get("/api/runs/{run_id}/events")
    async def get_events(run_id: str) -> JSONResponse:
        try:
            return JSONResponse([event.model_dump(mode="json") for event in snapshot_events(run_id)])
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @app.get("/api/runs/{run_id}/artifacts/{node_id}/{name}")
    async def get_artifact(run_id: str, node_id: str, name: str) -> PlainTextResponse:
        try:
            content = app.state.store.read_artifact_text(run_id, node_id, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid artifact path") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return PlainTextResponse(content)

    @app.get("/api/runs/{run_id}/artifacts/{node_id}/{name}/tail")
    async def artifact_tail(run_id: str, node_id: str, name: str,
                            limit: int = Query(50, ge=1, le=200),
                            before: int | None = Query(None, ge=0)) -> JSONResponse:
        try:
            page = app.state.store.read_artifact_tail(run_id, node_id, name, limit=limit, before=before)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return JSONResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str):
        try:
            snapshot_run(run_id)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

        async def event_stream():
            sent = 0
            while True:
                events = snapshot_events(run_id)
                for event in events[sent:]:
                    yield f"data: {event.model_dump_json()}\n\n"
                sent = len(events)
                if events and events[-1].type == "run_completed":
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/health")
    async def health() -> JSONResponse:
        runs = snapshot_runs()
        return JSONResponse(
            {
                "ok": True,
                "runs": {
                    "total": len(runs),
                    "queued": sum(run.status.value == "queued" for run in runs),
                    "running": sum(run.status.value in {"running", "cancelling"} for run in runs),
                },
            }
        )

    return app
