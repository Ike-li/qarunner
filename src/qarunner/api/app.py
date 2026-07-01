"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from qarunner.api.deps import Container, create_container
from qarunner.api.routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle — initialise container if not injected."""
    if not hasattr(app.state, "container") or app.state.container is None:
        app.state.container = create_container()
    container = app.state.container
    settings = container.settings
    # Initialize SQLite store
    await container.store.initialize()
    # Crash recovery: fail RUNNING runs left by a previous process.  QUEUED
    # runs survive restart — they are persistent and will be picked up by the
    # new poller (CONC-2: single-instance assumption).
    recover = getattr(container.store, "mark_interrupted_runs", None)
    if settings.crash_recovery_on_startup and recover is not None:
        interrupted = await recover(worker_node_id=settings.worker_node_id)

        if interrupted:
            logger.warning("Recovered %d interrupted run(s) as FAILED on startup", interrupted)
    # Start the persistent task scheduler (poller) before cron, so the poller
    # is ready to pick up any QUEUED runs that survived the restart.
    await container.task_scheduler.start()
    await container.scheduler.start()
    yield
    # Cleanup — stop cron triggers first, then stop the task poller,
    # drain in-flight runs, then close the DB.
    await container.scheduler.shutdown()
    await container.task_scheduler.shutdown()
    await container.orchestrator.drain(timeout=settings.shutdown_drain_timeout_seconds)
    await container.store.close()


def create_app(container: Container | None = None) -> FastAPI:
    """Build and return a configured ``FastAPI`` instance."""
    app = FastAPI(title="qarunner", lifespan=lifespan)
    app.include_router(router)
    if container is not None:
        app.state.container = container

    import os
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    static_dir = os.environ.get("QARUNNER_STATIC_ROOT")
    if static_dir:
        dist_path = Path(static_dir)
    else:
        # Resolve project root relative to this file: src/qarunner/api/app.py -> parents[3]
        project_root = Path(__file__).resolve().parents[3]
        dist_path = project_root / "frontend" / "dist"

    if dist_path.is_dir():
        app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="static")

    return app


app = create_app()

