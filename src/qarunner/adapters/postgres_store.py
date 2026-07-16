"""PostgreSQL persistence adapter with forward-only schema migrations."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import asyncpg

from qarunner.errors import RunNotFound
from qarunner.models import (
    Credential,
    ReportRef,
    Run,
    RunStatus,
    TestProfile,
    TestSuite,
    TestSummary,
)

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
    (
        3,
        """
        CREATE TABLE suites (
            name TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'local',
            repo_url TEXT,
            ref TEXT,
            credential_ref TEXT,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
    (
        4,
        """
        CREATE TABLE credentials (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            enc_secret TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
    (
        5,
        """
        CREATE TABLE test_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            tests_path TEXT NOT NULL,
            runner TEXT NOT NULL DEFAULT 'pytest',
            selected_files_json JSONB NOT NULL DEFAULT '[]',
            selected_markers_json JSONB NOT NULL DEFAULT '[]',
            extra_args TEXT NOT NULL DEFAULT '',
            executor_mode TEXT NOT NULL DEFAULT 'docker',
            timeout INTEGER,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            env_json JSONB NOT NULL DEFAULT '{}',
            webhook_url TEXT
        )
        """,
    ),
)
_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_ADMIN_MUTATION_LOCK_SQL = """
SELECT pg_advisory_xact_lock(
    hashtextextended('qarunner-admin-role-change:' || current_schema(), 0)
)
"""


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

    async def create_user(self, username: str, password_hash: str, role: str) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, token_version)
                VALUES ($1, $2, $3, $4, 0)
                """,
                username,
                password_hash,
                role,
                datetime.now(UTC),
            )

    async def list_users(self) -> list[dict[str, object]]:
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                "SELECT username, role, created_at FROM users ORDER BY username"
            )
        return [dict(record) for record in records]

    async def update_password(self, username: str, password_hash: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "UPDATE users SET password_hash = $1 WHERE username = $2",
                password_hash,
                username,
            )
        return status == "UPDATE 1"

    async def increment_token_version(self, username: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                """
                UPDATE users
                SET token_version = token_version + 1
                WHERE username = $1
                """,
                username,
            )
        return status == "UPDATE 1"

    async def update_role(self, username: str, role: str) -> bool:
        async with self._require_pool().acquire() as connection, connection.transaction():
            await connection.execute(_ADMIN_MUTATION_LOCK_SQL)
            exists = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM users WHERE username = $1)",
                username,
            )
            await connection.execute(
                """
                UPDATE users
                SET role = $1
                WHERE username = $2
                  AND (
                    role <> 'admin'
                    OR $1 = 'admin'
                    OR (SELECT count(*) FROM users WHERE role = 'admin') > 1
                  )
                """,
                role,
                username,
            )
        return bool(exists)

    async def delete_user(self, username: str) -> bool:
        async with self._require_pool().acquire() as connection, connection.transaction():
            await connection.execute(_ADMIN_MUTATION_LOCK_SQL)
            status = await connection.execute(
                """
                DELETE FROM users
                WHERE username = $1
                  AND (
                    role <> 'admin'
                    OR (SELECT count(*) FROM users WHERE role = 'admin') > 1
                  )
                """,
                username,
            )
        return status == "DELETE 1"

    async def demote_if_not_last_admin(self, username: str, new_role: str) -> bool:
        async with self._require_pool().acquire() as connection, connection.transaction():
            await connection.execute(_ADMIN_MUTATION_LOCK_SQL)
            status = await connection.execute(
                """
                UPDATE users
                SET role = $1
                WHERE username = $2
                  AND role = 'admin'
                  AND (SELECT count(*) FROM users WHERE role = 'admin') > 1
                """,
                new_role,
                username,
            )
        return status == "UPDATE 1"

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

    async def list(self, limit: int | None = None) -> list[Run]:
        query = "SELECT * FROM runs ORDER BY created_at DESC"
        async with self._require_pool().acquire() as connection:
            if limit is None:
                records = await connection.fetch(query)
            else:
                records = await connection.fetch(f"{query} LIMIT $1", limit)
        return [_record_to_run(record) for record in records]

    async def cancel_if_inflight(self, run_id: str, finished_at: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                """
                UPDATE runs
                SET status = 'cancelled', finished_at = $2
                WHERE id = $1 AND status IN ('queued', 'running')
                """,
                run_id,
                datetime.fromisoformat(finished_at),
            )
        return status == "UPDATE 1"

    async def count_inflight_runs(self, created_by: str) -> int:
        async with self._require_pool().acquire() as connection:
            count = await connection.fetchval(
                """
                SELECT count(*)
                FROM runs
                WHERE created_by = $1 AND status IN ('queued', 'running')
                """,
                created_by,
            )
        return int(count)

    async def delete_run(self, run_id: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute("DELETE FROM runs WHERE id = $1", run_id)
        return status == "DELETE 1"

    async def dequeue_next_queued(self) -> str | None:
        async with self._require_pool().acquire() as connection, connection.transaction():
            run_id = await connection.fetchval(
                """
                WITH candidate AS (
                    SELECT id
                    FROM runs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE runs
                SET status = 'running', started_at = now()
                FROM candidate
                WHERE runs.id = candidate.id
                RETURNING runs.id
                """
            )
        return None if run_id is None else str(run_id)

    async def lock_run(self, run_id: str, locked: bool) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                "UPDATE runs SET locked = $1 WHERE id = $2",
                locked,
                run_id,
            )

    async def save_suite(self, suite: TestSuite) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO suites (
                    name, source, repo_url, ref, credential_ref, created_by, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (name) DO UPDATE SET
                    source = excluded.source,
                    repo_url = excluded.repo_url,
                    ref = excluded.ref,
                    credential_ref = excluded.credential_ref,
                    created_by = excluded.created_by,
                    created_at = excluded.created_at
                """,
                suite.name,
                suite.source,
                suite.repo_url,
                suite.ref,
                suite.credential_ref,
                suite.created_by,
                suite.created_at,
            )

    async def get_suite(self, name: str) -> TestSuite | None:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow(
                """
                SELECT name, source, repo_url, ref, credential_ref, created_by, created_at
                FROM suites
                WHERE name = $1
                """,
                name,
            )
        return None if record is None else TestSuite.model_validate(dict(record))

    async def list_suites(self) -> list[TestSuite]:
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                """
                SELECT name, source, repo_url, ref, credential_ref, created_by, created_at
                FROM suites
                ORDER BY name
                """
            )
        return [TestSuite.model_validate(dict(record)) for record in records]

    async def delete_suite(self, name: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute("DELETE FROM suites WHERE name = $1", name)
        return status == "DELETE 1"

    async def save_credential(self, credential: Credential, encrypted_secret: str) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO credentials (id, name, type, enc_secret, created_by, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                credential.id,
                credential.name,
                credential.type,
                encrypted_secret,
                credential.created_by,
                credential.created_at,
            )

    async def get_credential(self, credential_id: str) -> Credential | None:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow(
                """
                SELECT id, name, type, created_by, created_at
                FROM credentials
                WHERE id = $1
                """,
                credential_id,
            )
        return None if record is None else Credential.model_validate(dict(record))

    async def get_credential_secret(self, credential_id: str) -> str | None:
        async with self._require_pool().acquire() as connection:
            secret = await connection.fetchval(
                "SELECT enc_secret FROM credentials WHERE id = $1",
                credential_id,
            )
        return None if secret is None else str(secret)

    async def list_credentials(self) -> list[Credential]:
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                """
                SELECT id, name, type, created_by, created_at
                FROM credentials
                ORDER BY created_at
                """
            )
        return [Credential.model_validate(dict(record)) for record in records]

    async def delete_credential(self, credential_id: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "DELETE FROM credentials WHERE id = $1", credential_id
            )
        return status == "DELETE 1"

    async def save_profile(self, profile: TestProfile) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO test_profiles (
                    id, name, description, tests_path, runner, selected_files_json,
                    selected_markers_json, extra_args, executor_mode, timeout, created_by,
                    created_at, env_json, webhook_url
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11, $12,
                    $13::jsonb, $14
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    tests_path = excluded.tests_path,
                    runner = excluded.runner,
                    selected_files_json = excluded.selected_files_json,
                    selected_markers_json = excluded.selected_markers_json,
                    extra_args = excluded.extra_args,
                    executor_mode = excluded.executor_mode,
                    timeout = excluded.timeout,
                    created_by = excluded.created_by,
                    created_at = excluded.created_at,
                    env_json = excluded.env_json,
                    webhook_url = excluded.webhook_url
                """,
                profile.id,
                profile.name,
                profile.description,
                profile.tests_path,
                profile.runner,
                json.dumps(profile.selected_files),
                json.dumps(profile.selected_markers),
                profile.extra_args,
                profile.executor_mode,
                profile.timeout,
                profile.created_by,
                profile.created_at,
                json.dumps(profile.env),
                profile.webhook_url,
            )

    async def get_profile(self, profile_id: str) -> TestProfile | None:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow(
                "SELECT * FROM test_profiles WHERE id = $1", profile_id
            )
        return None if record is None else _record_to_profile(record)

    async def list_profiles(self, tests_path: str | None = None) -> list[TestProfile]:
        async with self._require_pool().acquire() as connection:
            if tests_path:
                records = await connection.fetch(
                    """
                    SELECT *
                    FROM test_profiles
                    WHERE tests_path = $1
                    ORDER BY created_at DESC
                    """,
                    tests_path,
                )
            else:
                records = await connection.fetch(
                    "SELECT * FROM test_profiles ORDER BY created_at DESC"
                )
        return [_record_to_profile(record) for record in records]

    async def delete_profile(self, profile_id: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "DELETE FROM test_profiles WHERE id = $1", profile_id
            )
        return status == "DELETE 1"

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


def _record_to_profile(record: asyncpg.Record) -> TestProfile:
    return TestProfile(
        id=record["id"],
        name=record["name"],
        description=record["description"],
        tests_path=record["tests_path"],
        runner=record["runner"],
        selected_files=json.loads(record["selected_files_json"]),
        selected_markers=json.loads(record["selected_markers_json"]),
        extra_args=record["extra_args"],
        executor_mode=record["executor_mode"],
        timeout=record["timeout"],
        created_by=record["created_by"],
        created_at=record["created_at"],
        env=json.loads(record["env_json"]),
        webhook_url=record["webhook_url"],
    )
