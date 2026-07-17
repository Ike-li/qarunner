"""Cross-layer proof for the development PostgreSQL runtime wiring."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from starlette.testclient import TestClient

from qarunner.adapters.postgres_store import PostgresStore
from qarunner.api.app import create_app
from qarunner.api.deps import create_container
from qarunner.config import Settings


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
        client.portal.call(terminate_current_connection)

        recovered = client.get("/health")
        replacement_pid = client.portal.call(current_connection_pid)

    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ok"}
    assert len(terminated_pids) == 1
    assert replacement_pid != terminated_pids[0]
