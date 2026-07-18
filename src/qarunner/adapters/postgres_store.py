"""PostgreSQL persistence adapter with forward-only schema migrations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import asyncpg

from qarunner.core.flaky import FlakyPolicy, flakiness
from qarunner.errors import RunNotFound
from qarunner.migrations.runtime import validate_schema_revision
from qarunner.models import (
    CaseHistoryPoint,
    Credential,
    FailureDiagnosis,
    ReportRef,
    Run,
    RunStatus,
    TestCaseResult,
    TestProfile,
    TestSchedule,
    TestSuite,
    TestSummary,
)

logger = logging.getLogger(__name__)

_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_MAX_CASE_MESSAGE_CHARS = 8192
_ADMIN_MUTATION_LOCK_SQL = """
SELECT pg_advisory_xact_lock(
    hashtextextended('qarunner-admin-role-change:' || current_schema(), 0)
)
"""


class PostgresStore:
    """Application store backed by a bounded asyncpg connection pool."""

    def __init__(
        self,
        database_url: str,
        *,
        schema: str = "public",
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ) -> None:
        if _SCHEMA_PATTERN.fullmatch(schema) is None:
            raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
        if not 1 <= pool_min_size <= pool_max_size:
            raise ValueError("PostgreSQL pool sizes must satisfy 1 <= min <= max")
        self._database_url = database_url
        self._schema = schema
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Open the pool only after the operator-owned Schema is exactly at head."""
        async with self._initialization_lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        from qarunner.config import Settings
        from qarunner.core.auth import hash_password

        created_pool = self._pool is None
        if created_pool:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                server_settings={"search_path": self._schema},
            )

        try:
            async with self._require_pool().acquire() as connection, connection.transaction():
                await validate_schema_revision(connection, schema=self._schema)

                if created_pool:
                    await connection.execute(
                        "UPDATE runs SET cleanup_claimed = FALSE WHERE cleanup_claimed = TRUE"
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
        if record is None:
            return None
        user = dict(record)
        user["created_at"] = record["created_at"].isoformat()
        return user

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
        return [
            {
                "username": record["username"],
                "role": record["role"],
                "created_at": record["created_at"].isoformat(),
            }
            for record in records
        ]

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
            await self._save_run(connection, run)

    async def create_if_below_inflight_limit(self, run: Run, limit: int) -> bool:
        async with self._require_pool().acquire() as connection, connection.transaction():
            await connection.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(
                        'qarunner-inflight-run:' || current_database() || ':' ||
                        current_schema() || ':' || $1::text,
                        0
                    )
                )
                """,
                run.created_by,
            )
            count = await connection.fetchval(
                """
                SELECT count(*)
                FROM runs
                WHERE created_by = $1 AND status IN ('queued', 'running')
                """,
                run.created_by,
            )
            if int(count) >= limit:
                return False
            await self._save_run(connection, run)
        return True

    @staticmethod
    async def _save_run(connection: asyncpg.Connection, run: Run) -> None:
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
        query = "SELECT * FROM runs ORDER BY created_at DESC, id DESC"
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

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                """
                SELECT *
                FROM runs
                WHERE finished_at <= $1
                  AND locked = FALSE
                  AND cleanup_claimed = FALSE
                  AND status IN ('completed', 'failed', 'timeout')
                """,
                cutoff,
            )
        return [_record_to_run(record) for record in records]

    async def claim_run_cleanup(self, run_id: str, retention_days: int) -> bool:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                """
                UPDATE runs
                SET cleanup_claimed = TRUE
                WHERE id = $1
                  AND cleanup_claimed = FALSE
                  AND locked = FALSE
                  AND finished_at <= $2
                  AND status IN ('completed', 'failed', 'timeout')
                """,
                run_id,
                cutoff,
            )
        return status == "UPDATE 1"

    async def finish_run_cleanup(self, run_id: str, *, cleaned: bool) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                UPDATE runs
                SET report_json = CASE WHEN $2 THEN NULL ELSE report_json END,
                    cleanup_claimed = FALSE
                WHERE id = $1 AND cleanup_claimed = TRUE
                """,
                run_id,
                cleaned,
            )

    async def mark_interrupted_runs(self, worker_node_id: str | None = None) -> int:
        query = """
            UPDATE runs
            SET status = 'failed',
                error = 'interrupted by server restart',
                finished_at = now()
            WHERE status = 'running'
        """
        parameters: tuple[str, ...] = ()
        if worker_node_id is not None:
            query += " AND worker_node_id = $1"
            parameters = (worker_node_id,)
        query += " RETURNING id"
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(query, *parameters)
        return len(records)

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

    async def lock_run(self, run_id: str, locked: bool) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "UPDATE runs SET locked = $1 WHERE id = $2 AND cleanup_claimed = FALSE",
                locked,
                run_id,
            )
        return status == "UPDATE 1"

    async def save_cases(
        self,
        run_id: str,
        tests_path: str,
        created_at: datetime,
        cases: list[TestCaseResult],
    ) -> None:
        rows = [
            (
                run_id,
                tests_path,
                created_at,
                case.suite,
                case.name,
                case.status,
                case.duration_ms,
                case.message[:_MAX_CASE_MESSAGE_CHARS] if case.message else None,
            )
            for case in cases
        ]
        async with self._require_pool().acquire() as connection, connection.transaction():
            await connection.fetchval(
                "SELECT id FROM runs WHERE id = $1 FOR UPDATE",
                run_id,
            )
            await connection.execute("DELETE FROM run_test_cases WHERE run_id = $1", run_id)
            if rows:
                await connection.executemany(
                    """
                    INSERT INTO run_test_cases (
                        run_id, tests_path, created_at, suite, name, status, duration_ms, message
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    rows,
                )

    async def get_cases_for_run(self, run_id: str) -> list[TestCaseResult]:
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                """
                SELECT suite, name, status, duration_ms, message
                FROM run_test_cases
                WHERE run_id = $1
                ORDER BY id
                """,
                run_id,
            )
        return [TestCaseResult.model_validate(dict(record)) for record in records]

    async def get_case_history(
        self,
        tests_path: str,
        suite: str,
        name: str,
        limit: int = 20,
        created_by: str | None = None,
        profile_id: str | None = None,
    ) -> list[CaseHistoryPoint]:
        histories = await self.get_case_histories(
            tests_path,
            [(suite, name)],
            limit,
            created_by,
            profile_id,
        )
        return histories[(suite, name)]

    async def get_case_histories(
        self,
        tests_path: str,
        cases: list[tuple[str, str]],
        limit: int = 20,
        created_by: str | None = None,
        profile_id: str | None = None,
    ) -> dict[tuple[str, str], list[CaseHistoryPoint]]:
        result = {case: [] for case in cases}
        if not result:
            return result
        suites = [suite for suite, _ in result]
        names = [name for _, name in result]
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(
                """
                WITH requested(suite, name) AS (
                    SELECT DISTINCT suite, name
                    FROM unnest($2::text[], $3::text[]) AS input(suite, name)
                ),
                ranked AS (
                    SELECT
                        c.suite,
                        c.name,
                        c.created_at,
                        c.status,
                        c.run_id,
                        c.id,
                        row_number() OVER (
                            PARTITION BY c.suite, c.name
                            ORDER BY c.created_at DESC, c.run_id DESC, c.id DESC
                        ) AS recent_rank
                    FROM run_test_cases c
                    JOIN requested q ON q.suite = c.suite AND q.name = c.name
                    JOIN runs r ON r.id = c.run_id
                    WHERE c.tests_path = $1
                      AND ($4::text IS NULL OR r.created_by = $4)
                      AND ($5::text IS NULL OR r.profile_id = $5)
                )
                SELECT suite, name, created_at, status
                FROM ranked
                WHERE recent_rank <= $6
                ORDER BY suite, name, created_at, run_id, id
                """,
                tests_path,
                suites,
                names,
                created_by,
                profile_id,
                limit,
            )
        for record in records:
            result[(record["suite"], record["name"])].append(
                CaseHistoryPoint(
                    created_at=record["created_at"],
                    status=record["status"],
                )
            )
        return result

    async def count_flaky_tests(
        self,
        days: int = 30,
        created_by: str | None = None,
        *,
        min_observations: int = 4,
        flip_threshold: int = 3,
    ) -> int:
        since = datetime.now(UTC) - timedelta(days=days)
        query = """
            SELECT c.tests_path, c.suite, c.name, c.status
            FROM run_test_cases c
            JOIN runs r ON r.id = c.run_id
            WHERE r.status = 'completed' AND c.created_at >= $1
        """
        parameters: list[object] = [since]
        if created_by is not None:
            parameters.append(created_by)
            query += " AND r.created_by = $2"
        query += " ORDER BY c.tests_path, c.suite, c.name, c.created_at, c.run_id, c.id"
        async with self._require_pool().acquire() as connection:
            records = await connection.fetch(query, *parameters)
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for record in records:
            key = (record["tests_path"], record["suite"], record["name"])
            grouped.setdefault(key, []).append(record["status"])
        policy = FlakyPolicy(
            min_observations=min_observations,
            flip_threshold=flip_threshold,
        )
        return sum(1 for statuses in grouped.values() if flakiness(statuses, policy)[0])

    async def save_ai_diagnosis(
        self,
        run_id: str,
        diagnosis: FailureDiagnosis,
        provider: str,
        model: str,
        created_at: datetime,
    ) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO run_ai_diagnosis
                    (run_id, diagnosis_json, provider, model, created_at)
                VALUES ($1, $2::jsonb, $3, $4, $5)
                ON CONFLICT (run_id) DO UPDATE SET
                    diagnosis_json = EXCLUDED.diagnosis_json,
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    created_at = EXCLUDED.created_at
                """,
                run_id,
                diagnosis.model_dump_json(),
                provider,
                model,
                created_at,
            )

    async def get_ai_diagnosis(self, run_id: str) -> FailureDiagnosis | None:
        async with self._require_pool().acquire() as connection:
            value = await connection.fetchval(
                "SELECT diagnosis_json FROM run_ai_diagnosis WHERE run_id = $1",
                run_id,
            )
        if value is None:
            return None
        try:
            return FailureDiagnosis.model_validate_json(value)
        except Exception:  # noqa: BLE001 - corrupt/schema-drifted cache is a miss
            logger.warning(
                "Corrupt AI diagnosis cache for run %s; treating as missing",
                run_id,
                exc_info=True,
            )
            return None

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
                ORDER BY created_at ASC, id ASC
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
                    ORDER BY created_at DESC, id DESC
                    """,
                    tests_path,
                )
            else:
                records = await connection.fetch(
                    "SELECT * FROM test_profiles ORDER BY created_at DESC, id DESC"
                )
        return [_record_to_profile(record) for record in records]

    async def delete_profile(self, profile_id: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "DELETE FROM test_profiles WHERE id = $1", profile_id
            )
        return status == "DELETE 1"

    async def save_schedule(self, schedule: TestSchedule) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                """
                INSERT INTO test_schedules (
                    id, name, profile_id, cron_expression, enabled, timezone, last_run_at,
                    next_run_at, created_by, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    profile_id = excluded.profile_id,
                    cron_expression = excluded.cron_expression,
                    enabled = excluded.enabled,
                    timezone = excluded.timezone,
                    next_run_at = excluded.next_run_at,
                    created_by = excluded.created_by,
                    created_at = excluded.created_at
                """,
                schedule.id,
                schedule.name,
                schedule.profile_id,
                schedule.cron_expression,
                schedule.enabled,
                schedule.timezone,
                schedule.last_run_at,
                schedule.next_run_at,
                schedule.created_by,
                schedule.created_at,
            )

    async def get_schedule(self, schedule_id: str) -> TestSchedule | None:
        async with self._require_pool().acquire() as connection:
            record = await connection.fetchrow(
                "SELECT * FROM test_schedules WHERE id = $1", schedule_id
            )
        return None if record is None else _record_to_schedule(record)

    async def claim_schedule_run(self, schedule_id: str, fire_time: datetime) -> bool:
        normalized_fire_time = fire_time.astimezone(UTC)
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                """
                UPDATE test_schedules
                SET last_run_at = $2
                WHERE id = $1 AND (last_run_at IS NULL OR last_run_at < $2)
                """,
                schedule_id,
                normalized_fire_time,
            )
        return status == "UPDATE 1"

    async def list_schedules(self, profile_id: str | None = None) -> list[TestSchedule]:
        async with self._require_pool().acquire() as connection:
            if profile_id:
                records = await connection.fetch(
                    """
                    SELECT *
                    FROM test_schedules
                    WHERE profile_id = $1
                    ORDER BY created_at DESC, id DESC
                    """,
                    profile_id,
                )
            else:
                records = await connection.fetch(
                    "SELECT * FROM test_schedules ORDER BY created_at DESC, id DESC"
                )
        return [_record_to_schedule(record) for record in records]

    async def update_schedule_next_run(
        self, schedule_id: str, next_run_at: datetime | None
    ) -> None:
        async with self._require_pool().acquire() as connection:
            await connection.execute(
                "UPDATE test_schedules SET next_run_at = $2 WHERE id = $1",
                schedule_id,
                next_run_at,
            )

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self._require_pool().acquire() as connection:
            status = await connection.execute(
                "DELETE FROM test_schedules WHERE id = $1", schedule_id
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


def _record_to_schedule(record: asyncpg.Record) -> TestSchedule:
    return TestSchedule(
        id=record["id"],
        name=record["name"],
        profile_id=record["profile_id"],
        cron_expression=record["cron_expression"],
        enabled=record["enabled"],
        timezone=record["timezone"],
        last_run_at=record["last_run_at"],
        next_run_at=record["next_run_at"],
        created_by=record["created_by"],
        created_at=record["created_at"],
    )
