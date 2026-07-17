"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

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
    # BUG-12: warn when cookie_secure=False in production-like deployments.
    if not settings.cookie_secure:
        logger.warning(
            "QARUNNER_COOKIE_SECURE is False — auth cookies will be sent over "
            "plaintext HTTP. Set QARUNNER_COOKIE_SECURE=true for production."
        )
    async with AsyncExitStack() as cleanup:
        # Register each cleanup only after its startup stage succeeds. LIFO exit
        # preserves cron -> poller -> drain -> Store for both normal shutdown
        # and a later startup-stage failure.
        await container.store.initialize()
        cleanup.push_async_callback(container.store.close)

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
        cleanup.push_async_callback(
            container.orchestrator.drain,
            settings.shutdown_drain_timeout_seconds,
        )
        cleanup.push_async_callback(container.task_scheduler.shutdown)

        await container.scheduler.start()
        cleanup.push_async_callback(container.scheduler.shutdown)
        yield


def create_app(container: Container | None = None) -> FastAPI:
    """Build and return a configured ``FastAPI`` instance."""
    app = FastAPI(title="qarunner", lifespan=lifespan)
    app.include_router(router)

    # CORS — only add middleware when origins are configured. Same-origin
    # deployments (static files mounted at "/") don't need CORS at all.
    if container is not None:
        origins = [o.strip() for o in container.settings.cors_origins.split(",") if o.strip()]
        if origins:
            from fastapi.middleware.cors import CORSMiddleware

            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "PUT", "DELETE"],
                allow_headers=["Authorization", "Content-Type"],
            )

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
