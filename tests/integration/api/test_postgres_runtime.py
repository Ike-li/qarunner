"""Cross-layer proof for the development PostgreSQL runtime wiring."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from starlette.testclient import TestClient

from qarunner.adapters.postgres_store import PostgresStore
from qarunner.api.app import create_app
from qarunner.api.deps import create_container
from qarunner.config import Settings
from qarunner.models import (
    DiagnosisConfidence,
    FailureDiagnosis,
    ReportRef,
    RootCauseCategory,
    Run,
    RunStatus,
    TestCaseResult,
    TestSummary,
)


@pytest.fixture
def postgres_schema() -> Iterator[tuple[str, str]]:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    schema = f"test_{uuid4().hex}"

    async def create() -> None:
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            await connection.close()

    asyncio.run(create())
    try:
        yield database_url, schema
    finally:

        async def drop() -> None:
            connection = await asyncpg.connect(database_url)
            try:
                await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                await connection.close()

        asyncio.run(drop())


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/auth/login",
        json={
            "username": "admin",
            "password": os.environ["QARUNNER_ADMIN_PASSWORD"],
        },
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_postgres_container_lifespan_health_and_restart_persistence(
    postgres_schema: tuple[str, str], tmp_path: Path
) -> None:
    database_url, schema = postgres_schema
    settings = Settings(
        database_backend="postgres",
        database_url=database_url,
        database_schema=schema,
        crash_recovery_on_startup=False,
        tests_root=str(tmp_path),
        artifacts_root=str(tmp_path),
    )
    container = create_container(settings)

    assert isinstance(container.store, PostgresStore)
    assert container.store._schema == schema
    with TestClient(create_app(container)) as client:
        response = client.get("/health")
        login = client.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": os.environ["QARUNNER_ADMIN_PASSWORD"],
            },
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        created = client.post(
            "/users",
            json={
                "username": "runtime-persisted",
                "password": "runtime-user-password",
                "role": "user",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert created.status_code == 201

    async def read_after_restart() -> dict[str, object] | None:
        restarted = PostgresStore(database_url, schema=schema)
        try:
            await restarted.initialize()
            return await restarted.get_user("runtime-persisted")
        finally:
            await restarted.close()

    persisted_user = asyncio.run(read_after_restart())
    assert persisted_user is not None
    assert persisted_user["role"] == "user"


def test_postgres_profile_and_schedule_survive_container_restart(
    postgres_schema: tuple[str, str], tmp_path: Path
) -> None:
    database_url, schema = postgres_schema
    settings = Settings(
        database_backend="postgres",
        database_url=database_url,
        database_schema=schema,
        crash_recovery_on_startup=False,
        tests_root=str(tmp_path),
        artifacts_root=str(tmp_path),
    )

    with TestClient(create_app(create_container(settings))) as client:
        headers = _admin_headers(client)
        profile_response = client.post(
            "/profiles",
            headers=headers,
            json={
                "name": "Restart profile",
                "description": "PostgreSQL runtime persistence proof",
                "tests_path": "runtime-suite",
                "runner": "pytest",
                "selected_files": ["tests/test_runtime.py"],
                "selected_markers": ["smoke"],
                "extra_args": "-q",
                "executor_mode": "docker",
                "timeout": 120,
                "env": {"TARGET": "restart-proof"},
            },
        )
        assert profile_response.status_code == 201
        profile = profile_response.json()

        schedule_response = client.post(
            "/schedules",
            headers=headers,
            json={
                "name": "Restart schedule",
                "profile_id": profile["id"],
                "cron_expression": "0 2 * * *",
                "enabled": True,
                "timezone": "America/Chicago",
            },
        )
        assert schedule_response.status_code == 201
        schedule = schedule_response.json()

    with TestClient(create_app(create_container(settings))) as restarted_client:
        restarted_headers = _admin_headers(restarted_client)
        profiles = restarted_client.get("/profiles", headers=restarted_headers)
        schedules = restarted_client.get("/schedules", headers=restarted_headers)
        persisted_schedule = restarted_client.get(
            f"/schedules/{schedule['id']}", headers=restarted_headers
        )

    assert profiles.status_code == 200
    assert profiles.json() == [profile]
    assert schedules.status_code == 200
    assert len(schedules.json()) == 1
    persisted_schedule_body = schedules.json()[0]
    for field in (
        "id",
        "name",
        "profile_id",
        "cron_expression",
        "enabled",
        "timezone",
        "created_by",
        "created_at",
    ):
        assert persisted_schedule_body[field] == schedule[field]
    assert persisted_schedule_body["last_run_at"] is None
    assert persisted_schedule_body["next_run_at"] is not None
    assert persisted_schedule.status_code == 200
    assert persisted_schedule.json() == persisted_schedule_body


def test_postgres_run_evidence_and_cleanup_survive_runtime_restart(
    postgres_schema: tuple[str, str], tmp_path: Path
) -> None:
    """Prove Store-seeded evidence is readable and cleanable after a runtime restart.

    This does not exercise runner, collector, or LLM generation; those producer
    chains have separate tests. The boundary here is persistence plus API projection.
    """
    database_url, schema = postgres_schema
    settings = Settings(
        database_backend="postgres",
        database_url=database_url,
        database_schema=schema,
        crash_recovery_on_startup=False,
        tests_root=str(tmp_path),
        artifacts_root=str(tmp_path),
    )
    container = create_container(settings)
    old_date = datetime.now(UTC) - timedelta(days=40)
    run = Run(
        id="runtime-evidence",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="admin",
        tests_path="runtime-suite",
        summary=TestSummary(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            error=0,
            duration_ms=25,
        ),
        report=ReportRef(
            allure_results_dir="runtime-evidence/results",
            allure_report_file="runtime-evidence/report/index.html",
            html_generated=True,
        ),
        created_at=old_date,
        finished_at=old_date,
    )
    cases = [
        TestCaseResult(
            suite="runtime",
            name="test_restart",
            status="failed",
            duration_ms=25,
            message="persisted failure",
        )
    ]
    diagnosis = FailureDiagnosis(
        category=RootCauseCategory.ASSERTION,
        confidence=DiagnosisConfidence.HIGH,
        summary="persisted diagnosis",
        evidence=["assertion mismatch"],
    )
    artifact_dir = tmp_path / run.id
    artifact_dir.mkdir()
    (artifact_dir / "stdout.log").write_text("persisted log", encoding="utf-8")

    async def seed_evidence() -> None:
        await container.store.save(run)
        await container.store.save_cases(run.id, run.tests_path, run.created_at, cases)
        await container.store.save_ai_diagnosis(
            run.id,
            diagnosis,
            provider="runtime-proof",
            model="deterministic",
            created_at=run.finished_at,
        )

    with TestClient(create_app(container)) as client:
        assert client.portal is not None
        client.portal.call(seed_evidence)

    with TestClient(create_app(create_container(settings))) as restarted_client:
        headers = _admin_headers(restarted_client)
        persisted_run = restarted_client.get(f"/runs/{run.id}", headers=headers)
        persisted_diagnosis = restarted_client.get(f"/runs/{run.id}/ai-analysis", headers=headers)
        cleanup = restarted_client.post("/runs/cleanup?retention_days=30", headers=headers)
        cleaned_run = restarted_client.get(f"/runs/{run.id}", headers=headers)

    assert persisted_run.status_code == 200
    assert persisted_run.json()["cases"] == [case.model_dump() for case in cases]
    assert persisted_diagnosis.status_code == 200
    assert persisted_diagnosis.json()["diagnosis"]["summary"] == diagnosis.summary
    assert cleanup.status_code == 200
    assert cleanup.json() == {"status": "success", "cleaned_runs": 1}
    assert artifact_dir.exists() is False
    assert cleaned_run.status_code == 200
    assert cleaned_run.json()["report"] is None


def test_postgres_health_returns_503_after_pool_disconnect(
    postgres_schema: tuple[str, str], tmp_path: Path
) -> None:
    database_url, schema = postgres_schema
    container = create_container(
        Settings(
            database_backend="postgres",
            database_url=database_url,
            database_schema=schema,
            crash_recovery_on_startup=False,
            tests_root=str(tmp_path),
            artifacts_root=str(tmp_path),
        )
    )

    with TestClient(create_app(container)) as client:
        assert client.get("/health").status_code == 200
        assert client.portal is not None
        client.portal.call(container.task_scheduler.shutdown)
        client.portal.call(container.store.close)

        disconnected = client.get("/health")

    assert disconnected.status_code == 503
    assert disconnected.json()["detail"] == "database unavailable"


def test_postgres_health_recovers_after_connection_is_terminated(
    postgres_schema: tuple[str, str], tmp_path: Path
) -> None:
    database_url, schema = postgres_schema
    container = create_container(
        Settings(
            database_backend="postgres",
            database_url=database_url,
            database_schema=schema,
            database_pool_min_size=1,
            database_pool_max_size=1,
            crash_recovery_on_startup=False,
            tests_root=str(tmp_path),
            artifacts_root=str(tmp_path),
        )
    )
    terminated_pids: list[int] = []

    async def terminate_current_connection() -> None:
        assert isinstance(container.store, PostgresStore)
        pool = container.store._require_pool()
        with pytest.raises(asyncpg.ConnectionDoesNotExistError):
            async with pool.acquire() as connection:
                terminated_pids.append(await connection.fetchval("SELECT pg_backend_pid()"))
                await connection.fetchval("SELECT pg_terminate_backend(pg_backend_pid())")

    async def current_connection_pid() -> int:
        assert isinstance(container.store, PostgresStore)
        async with container.store._require_pool().acquire() as connection:
            return int(await connection.fetchval("SELECT pg_backend_pid()"))

    with TestClient(create_app(container)) as client:
        assert client.get("/health").status_code == 200
        assert client.portal is not None
        assert container.store._require_pool().get_min_size() == 1
        assert container.store._require_pool().get_max_size() == 1
        client.portal.call(terminate_current_connection)

        recovered = client.get("/health")
        replacement_pid = client.portal.call(current_connection_pid)

    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ok"}
    assert len(terminated_pids) == 1
    assert replacement_pid != terminated_pids[0]


def test_postgres_health_timeout_cancels_query_without_poisoning_pool(
    postgres_schema: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url, schema = postgres_schema
    container = create_container(
        Settings(
            database_backend="postgres",
            database_url=database_url,
            database_schema=schema,
            database_health_timeout_seconds=0.01,
            crash_recovery_on_startup=False,
            tests_root=str(tmp_path),
            artifacts_root=str(tmp_path),
        )
    )
    assert isinstance(container.store, PostgresStore)
    original_get_user = container.store.get_user

    async def slow_get_user(_username: str) -> None:
        async with container.store._require_pool().acquire() as connection:
            await connection.execute("SELECT pg_sleep(0.1)")

    with TestClient(create_app(container)) as client:
        monkeypatch.setattr(container.store, "get_user", slow_get_user)
        timed_out = client.get("/health")
        monkeypatch.setattr(container.store, "get_user", original_get_user)

        recovered = client.get("/health")

    assert timed_out.status_code == 503
    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ok"}
