"""PostgreSQL persistence adapter with forward-only schema migrations."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import asyncpg

from qarunner.errors import RunNotFound
from qarunner.models import ReportRef, Run, RunStatus, TestSummary

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            token_version INTEGER NOT NULL DEFAULT 0
        )
        """,
    ),
    (
        2,
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            runner TEXT NOT NULL,
            created_by TEXT NOT NULL,
            tests_path TEXT NOT NULL,
            args_json JSONB NOT NULL DEFAULT '[]',
            allure_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            timeout INTEGER,
            executor_mode TEXT NOT NULL DEFAULT 'docker',
            summary_json JSONB,
            report_json JSONB,
            exit_code INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            env_json JSONB NOT NULL DEFAULT '{}',
            locked BOOLEAN NOT NULL DEFAULT FALSE,
            worker_node_id TEXT,
            profile_id TEXT
        )
        """,
    ),
)
_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")


class PostgresStore:
    """Application store backed by a bounded asyncpg connection pool."""

    def __init__(self, database_url: str, *, schema: str = "public") -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
        self._database_url = database_url
        self._schema = schema
        self._pool: asyncpg.Pool | None = None
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open the pool, migrate an empty database, and seed the initial admin."""
        async with self._initialization_lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        from qarunner.config import Settings
        from qarunner.core.auth import hash_password

        created_pool = self._pool is None
        if created_pool:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                server_settings={"search_path": self._schema},
            )

        try:
            async with self._require_pool().acquire() as connection, connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('qarunner-schema-migrations'))"
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                applied = {
                    record["version"]
                    for record in await connection.fetch("SELECT version FROM schema_migrations")
                }
                for version, ddl in _MIGRATIONS:
                    if version not in applied:
                        await connection.execute(ddl)
                        await connection.execute(
                            "INSERT INTO schema_migrations (version) VALUES ($1)", version
                        )

                settings = Settings()
                await connection.execute(
                    """
                    INSERT INTO users (username, password_hash, role, created_at, token_version)
                    VALUES ($1, $2, 'admin', $3, 0)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    settings.admin_user,
                    hash_password(settings.admin_password),
                    datetime.now(UTC),
                )
        except BaseException:
            if created_pool:
                await self.close()
            raise

    async def get_user(self, username: str) -> dict[str, object] | None:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow(
                """
                SELECT username, password_hash, role, created_at, token_version
                FROM users
                WHERE username = $1
                """,
                username,
            )
        return None if record is None else dict(record)

    async def save(self, run: Run) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO runs (
                    id, status, runner, created_by, tests_path, args_json, allure_enabled,
                    timeout, executor_mode, summary_json, report_json, exit_code, error,
                    created_at, started_at, finished_at, env_json, locked, worker_node_id,
                    profile_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::jsonb,
                    $11::jsonb, $12, $13, $14, $15, $16, $17::jsonb, $18, $19, $20
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = excluded.status,
                    runner = excluded.runner,
                    created_by = excluded.created_by,
                    tests_path = excluded.tests_path,
                    args_json = excluded.args_json,
                    allure_enabled = excluded.allure_enabled,
                    timeout = excluded.timeout,
                    executor_mode = excluded.executor_mode,
                    summary_json = excluded.summary_json,
                    report_json = excluded.report_json,
                    exit_code = excluded.exit_code,
                    error = excluded.error,
                    created_at = excluded.created_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    env_json = excluded.env_json,
                    worker_node_id = excluded.worker_node_id,
                    profile_id = excluded.profile_id
                """,
                run.id,
                run.status.value,
                run.runner,
                run.created_by,
                run.tests_path,
                json.dumps(run.args),
                run.allure_enabled,
                run.timeout,
                run.executor_mode,
                None if run.summary is None else run.summary.model_dump_json(),
                None if run.report is None else run.report.model_dump_json(),
                run.exit_code,
                run.error,
                run.created_at,
                run.started_at,
                run.finished_at,
                json.dumps(run.env),
                run.locked,
                run.worker_node_id,
                run.profile_id,
            )

    async def get(self, run_id: str) -> Run:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        if record is None:
            raise RunNotFound(run_id)
        return _record_to_run(record)

    async def lock_run(self, run_id: str, locked: bool) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                "UPDATE runs SET locked = $1 WHERE id = $2",
                locked,
                run_id,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresStore is not initialized")
        return self._pool


def _record_to_run(record: asyncpg.Record) -> Run:
    summary_data = json.loads(record["summary_json"]) if record["summary_json"] else None
    report_data = json.loads(record["report_json"]) if record["report_json"] else None
    return Run(
        id=record["id"],
        status=RunStatus(record["status"]),
        runner=record["runner"],
        created_by=record["created_by"],
        tests_path=record["tests_path"],
        args=json.loads(record["args_json"]),
        allure_enabled=record["allure_enabled"],
        timeout=record["timeout"],
        executor_mode=record["executor_mode"],
        summary=TestSummary.model_validate(summary_data) if summary_data else None,
        report=ReportRef.model_validate(report_data) if report_data else None,
        exit_code=record["exit_code"],
        error=record["error"],
        created_at=record["created_at"],
        started_at=record["started_at"],
        finished_at=record["finished_at"],
        env=json.loads(record["env_json"]),
        locked=record["locked"],
        worker_node_id=record["worker_node_id"],
        profile_id=record["profile_id"],
    )
