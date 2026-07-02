"""FastAPI application factory for the WingGen Studio local web server.

Implements the REST/SSE API frozen in ``STUDIO_CONTRACT.md`` section 7. The
server creates store runs and spawns runner subprocesses; it never performs
optimization work in-process.

Runs-root resolution order: explicit ``runs_root`` argument, then the
``WINGGEN_RUNS_ROOT`` environment variable, then ``studio.runs_root`` from
the config file. Relative paths resolve against the repository root.
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import wingopt
from wingopt.config import load_config
from wingopt.store import RunStore
from wingopt.studio.jobs import STREAM_END, Job, JobManager
from wingopt.studio.meshes import MeshError, build_design_mesh
from wingopt.studio.schema import build_param_schema

REPO_ROOT = Path(__file__).resolve().parents[3]


class JobRequest(BaseModel):
    """POST /api/jobs request body."""

    kind: str
    label: str = ""
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    simulate: dict[str, Any] = Field(default_factory=dict)
    sweep: dict[str, Any] | None = None
    optimize: dict[str, Any] | None = None


def _metal_available() -> bool:
    try:
        import mlx.core as mx

        return bool(mx.metal.is_available())
    except Exception:
        return False


def _resolve_runs_root(config_runs_root: str, override: Path | str | None) -> Path:
    candidate = override or os.environ.get("WINGGEN_RUNS_ROOT") or config_runs_root
    path = Path(candidate)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _sse_line(data: str) -> str:
    return f"data: {data}\n\n"


def create_app(config_path: Path, runs_root: Path | str | None = None) -> FastAPI:
    """Build the Studio FastAPI application.

    Args:
        config_path: Path to the simulator TOML config.
        runs_root: Optional override for the run-store root directory
            (takes precedence over ``WINGGEN_RUNS_ROOT`` and the config).

    Returns:
        Configured FastAPI app with a ``JobManager`` on ``app.state.jobs``.
    """
    config_path = Path(config_path)
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    config = load_config(config_path)
    resolved_runs_root = _resolve_runs_root(config.studio.runs_root, runs_root)
    resolved_runs_root.mkdir(parents=True, exist_ok=True)
    store = RunStore(resolved_runs_root)
    manager = JobManager(
        store=store,
        repo_root=REPO_ROOT,
        default_config_path=config_path,
    )
    data_dir = REPO_ROOT / "data"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.attach_loop(asyncio.get_running_loop())
        yield
        manager.shutdown()

    app = FastAPI(title="WingGen Studio", version=wingopt.__version__, lifespan=lifespan)
    app.state.jobs = manager
    app.state.store = store
    app.state.config = config

    # ------------------------------------------------------------- meta

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": wingopt.__version__,
            "metal_available": _metal_available(),
        }

    @app.get("/api/schema/params")
    def schema_params() -> list[dict[str, Any]]:
        return build_param_schema(config)

    @app.get("/api/config/default")
    def config_default() -> dict[str, Any]:
        with config_path.open("rb") as handle:
            return tomllib.load(handle)

    # ------------------------------------------------------------- jobs

    @app.post("/api/jobs", status_code=202)
    def create_job(request: JobRequest) -> dict[str, str]:
        kind = request.kind
        if kind not in ("simulate", "sweep", "optimize"):
            raise HTTPException(status_code=422, detail=f"Unknown job kind: {kind!r}")
        spec: dict[str, Any] | None = None
        if kind == "sweep":
            spec = request.sweep
        elif kind == "optimize":
            spec = request.optimize
        if kind in ("sweep", "optimize") and spec is None:
            raise HTTPException(status_code=422, detail=f"Job kind {kind!r} requires a spec")
        try:
            job = manager.submit(
                kind=kind,
                label=request.label,
                config_overrides=request.config_overrides,
                simulate_options=request.simulate,
                spec=spec,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"job_id": job.job_id, "run_id": job.run_id}

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return manager.list_jobs()

    def _job_or_404(job_id: str) -> Job:
        try:
            return manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return _job_or_404(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        _job_or_404(job_id)
        return manager.cancel(job_id).to_dict()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, replay: int = Query(default=1)) -> StreamingResponse:
        _job_or_404(job_id)

        async def stream():
            snapshot, queue = manager.subscribe(job_id)
            try:
                if replay:
                    for line in snapshot:
                        yield _sse_line(line)
                if queue is not None:
                    while True:
                        item = await queue.get()
                        if item is STREAM_END:
                            break
                        yield _sse_line(item)
                yield "event: end\ndata: {}\n\n"
            finally:
                if queue is not None:
                    manager.unsubscribe(job_id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # ------------------------------------------------------------- runs

    @app.get("/api/runs")
    def list_runs(kind: str | None = None) -> list[dict[str, Any]]:
        return [asdict(record) for record in store.list_runs(kind=kind)]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return asdict(store.get_run(run_id))
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}") from exc

    # ----------------------------------------------------------- designs

    @app.get("/api/designs")
    def list_designs(
        run_id: str | None = None,
        feasible: int = 0,
        sort: str = "score",
        order: str = "desc",
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        try:
            records = store.list_designs(
                run_id=run_id,
                feasible_only=bool(feasible),
                sort_by=sort,
                descending=order != "asc",
                limit=limit,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [asdict(record) for record in records]

    def _design_or_404(design_id: str):
        try:
            return store.get_design(design_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f"Unknown design: {design_id}") from exc

    @app.get("/api/designs/{design_id}")
    def get_design(design_id: str) -> dict[str, Any]:
        return asdict(_design_or_404(design_id))

    @app.get("/api/designs/{design_id}/mesh.stl")
    def design_mesh(
        design_id: str,
        span_sections: int = Query(default=121),
        profile_points: int = Query(default=241),
    ) -> FileResponse:
        design = _design_or_404(design_id)
        try:
            stl_path = build_design_mesh(
                store=store,
                design=design,
                default_config=config,
                data_dir=data_dir,
                span_sections=span_sections,
                profile_points=profile_points,
            )
        except MeshError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            stl_path,
            media_type="model/stl",
            filename=f"{design_id}.stl",
        )

    @app.get("/api/designs/{design_id}/export.json")
    def design_export(design_id: str) -> JSONResponse:
        design = _design_or_404(design_id)
        return JSONResponse(
            content=asdict(design),
            headers={
                "Content-Disposition": f'attachment; filename="{design_id}.json"',
            },
        )

    # ------------------------------------------------------------ static

    dist_dir = REPO_ROOT / "ui" / "web" / "dist"
    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="ui")
    else:

        @app.get("/")
        def root_hint() -> dict[str, str]:
            return {
                "status": "api-only",
                "hint": "Frontend not built. Run `npm run build` in ui/web, "
                "or use the /api endpoints directly.",
            }

    return app
