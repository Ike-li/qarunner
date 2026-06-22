"""Real run store backed by aiosqlite, one connection per operation."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from qarunner.errors import RunNotFound
from qarunner.models import ReportRef, Run, RunStatus, TestProfile, TestSchedule, TestSummary

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    runner        TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT 'system',
    tests_path    TEXT NOT NULL,
    args_json     TEXT NOT NULL DEFAULT '[]',
    allure_enabled INTEGER NOT NULL DEFAULT 1,
    timeout       INTEGER,
    executor_mode TEXT NOT NULL DEFAULT 'subprocess',
    summary_json  TEXT,
    report_json   TEXT,
    exit_code     INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    env_json      TEXT NOT NULL DEFAULT '{}',
    locked        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS test_profiles (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    tests_path    TEXT NOT NULL,
    selected_files TEXT NOT NULL DEFAULT '[]',
    selected_markers TEXT NOT NULL DEFAULT '[]',
    extra_args    TEXT NOT NULL DEFAULT '',
    executor_mode TEXT NOT NULL DEFAULT 'subprocess',
    timeout       INTEGER,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    env_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS test_schedules (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    last_run_at     TEXT,
    next_run_at     TEXT,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY(profile_id) REFERENCES test_profiles(id) ON DELETE CASCADE
);
"""


class SqliteStore:
    """RunStore backed by aiosqlite, opening a fresh connection per operation.

    A single shared connection is unsafe under concurrent async requests — cursor
    state and commits interleave (DATA-1). Each operation instead opens, uses, and
    closes its own connection; WAL mode (set once during ``initialize``) keeps
    file-backed reads and writes from blocking each other.

    For ``:memory:`` (used in tests) a plain in-memory DB would be invisible across
    per-op connections, so a uniquely-named shared-cache in-memory DB is used and
    kept alive by a single keepalive connection — independent per-op connections
    then observe the same data while staying isolated from other store instances.
    """

    def __init__(self, db_path: str) -> None:
        self._uri = False
        self._keepalive: aiosqlite.Connection | None = None
        if db_path == ":memory:":
            self._db_path = f"file:qarunner_mem_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
        else:
            self._db_path = db_path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a fresh connection with per-connection PRAGMAs applied."""
        db = await aiosqlite.connect(self._db_path, uri=self._uri)
        try:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("PRAGMA foreign_keys = ON")
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """Create schema, run migrations, enable WAL, and seed the admin user.

        The whole setup runs inside one ``BEGIN IMMEDIATE`` transaction so that
        concurrent first-starts (CONC-2) serialise on the write lock via
        busy_timeout instead of dead-locking on a read→write upgrade (which SQLite
        surfaces as "database is locked" without honouring the timeout). Every
        step is idempotent: ``CREATE ... IF NOT EXISTS``, tolerated ALTERs, and an
        ``INSERT OR IGNORE`` seed.
        """
        from qarunner.config import Settings
        from qarunner.core.auth import hash_password

        if not self._uri:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Keep the shared in-memory DB alive for the store's lifetime.
        if self._uri:
            self._keepalive = await aiosqlite.connect(self._db_path, uri=self._uri)

        async with self._connect() as db:
            await self._enable_wal(db)
            await db.execute("BEGIN IMMEDIATE")
            for raw_statement in _SCHEMA.split(";"):
                statement = raw_statement.strip()
                if statement:
                    await db.execute(statement)
            await self._migrate(db)

            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
            if row and row[0] == 0:
                settings = Settings()
                await db.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        settings.admin_user,
                        hash_password(settings.admin_password),
                        "admin",
                        datetime.now(UTC).isoformat(),
                    ),
                )
            await db.commit()

    @staticmethod
    async def _migrate(db: aiosqlite.Connection) -> None:
        """Apply idempotent ALTER migrations so legacy databases gain new columns.

        Runs inside ``initialize``'s transaction; the caller commits.
        """
        async with db.execute("PRAGMA table_info(runs)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        run_migrations = [
            (
                "created_by",
                "ALTER TABLE runs ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system'",
            ),
            (
                "executor_mode",
                "ALTER TABLE runs ADD COLUMN executor_mode TEXT NOT NULL DEFAULT 'subprocess'",
            ),
            ("env_json", "ALTER TABLE runs ADD COLUMN env_json TEXT NOT NULL DEFAULT '{}'"),
            ("locked", "ALTER TABLE runs ADD COLUMN locked INTEGER NOT NULL DEFAULT 0"),
        ]
        for column, ddl in run_migrations:
            if column not in columns:
                await SqliteStore._safe_alter(db, "runs", column, ddl)

        async with db.execute("PRAGMA table_info(test_profiles)") as cursor:
            profile_columns = [row[1] for row in await cursor.fetchall()]
        if "env_json" not in profile_columns:
            await SqliteStore._safe_alter(
                db,
                "test_profiles",
                "env_json",
                "ALTER TABLE test_profiles ADD COLUMN env_json TEXT NOT NULL DEFAULT '{}'",
            )

    @staticmethod
    async def _enable_wal(db: aiosqlite.Connection) -> None:
        """Switch to WAL mode, tolerating a concurrent first-start race (CONC-2).

        Converting the journal mode needs an exclusive lock and — unlike ordinary
        writes — does not honour busy_timeout, so two processes initialising a
        fresh DB at once can collide. journal_mode is a persistent file-level
        property, so once any initializer wins the conversion the DB is in WAL and
        the loser may safely proceed (its later writes busy-wait normally).
        """
        try:
            await db.execute("PRAGMA journal_mode=WAL")
        except aiosqlite.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            logger.debug("WAL conversion contended by a concurrent initializer; tolerating")

    @staticmethod
    async def _safe_alter(db: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
        """Run an ADD COLUMN migration, tolerating a concurrent first-start race.

        With several processes initialising an empty DB at once (CONC-2), two may
        both observe the column missing and both issue the ALTER; the loser raises
        an OperationalError. That is benign as long as the column ends up present,
        so re-check before swallowing and re-raise any genuine failure.
        """
        try:
            await db.execute(ddl)
        except aiosqlite.OperationalError:
            async with db.execute(f"PRAGMA table_info({table})") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
            if column not in columns:
                raise
            logger.debug("Column %s.%s added concurrently; tolerating ALTER race", table, column)

    async def get_user(self, username: str) -> dict | None:
        """Retrieve a user and their password hash from the database."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT username, password_hash, role, created_at FROM users WHERE username = ?",
                (username,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "username": row[0],
            "password_hash": row[1],
            "role": row[2],
            "created_at": row[3],
        }

    async def create_user(self, username: str, password_hash: str, role: str) -> None:
        """Insert a new user into the database."""
        now_str = datetime.now(UTC).isoformat()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, password_hash, role, now_str),
            )
            await db.commit()

    async def list_users(self) -> list[dict]:
        """List all users in the database."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT username, role, created_at FROM users ORDER BY username ASC"
            )
            rows = await cursor.fetchall()
        return [
            {
                "username": r[0],
                "role": r[1],
                "created_at": r[2],
            }
            for r in rows
        ]

    async def save(self, run: Run) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO runs "
                "(id, status, runner, created_by, tests_path, args_json, allure_enabled, "
                "timeout, executor_mode, summary_json, report_json, exit_code, error, "
                "created_at, started_at, finished_at, env_json, locked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.status.value,
                    run.runner,
                    run.created_by,
                    run.tests_path,
                    json.dumps(run.args),
                    int(run.allure_enabled),
                    run.timeout,
                    run.executor_mode,
                    json.dumps(run.summary.model_dump()) if run.summary else None,
                    json.dumps(run.report.model_dump()) if run.report else None,
                    run.exit_code,
                    run.error,
                    _dt_to_iso(run.created_at),
                    _dt_to_iso(run.started_at),
                    _dt_to_iso(run.finished_at),
                    json.dumps(run.env),
                    1 if run.locked else 0,
                ),
            )
            await db.commit()

    async def get(self, run_id: str) -> Run:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, status, runner, created_by, tests_path, args_json, allure_enabled, "
                "timeout, executor_mode, summary_json, report_json, exit_code, error, "
                "created_at, started_at, finished_at, env_json, locked "
                "FROM runs WHERE id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return _row_to_run(row)

    async def list(self) -> list[Run]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, status, runner, created_by, tests_path, args_json, allure_enabled, "
                "timeout, executor_mode, summary_json, report_json, exit_code, error, "
                "created_at, started_at, finished_at, env_json, locked "
                "FROM runs ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [_row_to_run(row) for row in rows]

    async def save_profile(self, profile: TestProfile) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO test_profiles ("
                "id, name, description, tests_path, selected_files, selected_markers, "
                "extra_args, executor_mode, timeout, created_by, created_at, env_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    profile.id,
                    profile.name,
                    profile.description,
                    profile.tests_path,
                    json.dumps(profile.selected_files),
                    json.dumps(profile.selected_markers),
                    profile.extra_args,
                    profile.executor_mode,
                    profile.timeout,
                    profile.created_by,
                    _dt_to_iso(profile.created_at),
                    json.dumps(profile.env),
                ),
            )
            await db.commit()

    async def get_profile(self, profile_id: str) -> TestProfile | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, name, description, tests_path, selected_files, selected_markers, "
                "extra_args, executor_mode, timeout, created_by, created_at, env_json "
                "FROM test_profiles WHERE id = ?",
                (profile_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_profile(row)

    async def list_profiles(self, tests_path: str | None = None) -> list[TestProfile]:
        async with self._connect() as db:
            if tests_path:
                cursor = await db.execute(
                    "SELECT id, name, description, tests_path, selected_files, selected_markers, "
                    "extra_args, executor_mode, timeout, created_by, created_at, env_json "
                    "FROM test_profiles WHERE tests_path = ? ORDER BY created_at DESC",
                    (tests_path,),
                )
            else:
                cursor = await db.execute(
                    "SELECT id, name, description, tests_path, selected_files, selected_markers, "
                    "extra_args, executor_mode, timeout, created_by, created_at, env_json "
                    "FROM test_profiles ORDER BY created_at DESC"
                )
            rows = await cursor.fetchall()
        return [_row_to_profile(row) for row in rows]

    async def lock_run(self, run_id: str, locked: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE runs SET locked = ? WHERE id = ?",
                (1 if locked else 0, run_id),
            )
            await db.commit()

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]:
        from datetime import timedelta
        cutoff_iso = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, status, runner, created_by, tests_path, args_json, allure_enabled, "
                "timeout, executor_mode, summary_json, report_json, exit_code, error, "
                "created_at, started_at, finished_at, env_json, locked "
                "FROM runs "
                "WHERE finished_at <= ? AND locked = 0 "
                "AND status IN ('completed', 'failed', 'timeout')",
                (cutoff_iso,),
            )
            rows = await cursor.fetchall()
        return [_row_to_run(row) for row in rows]

    async def mark_interrupted_runs(self) -> int:
        """Fail runs left QUEUED/RUNNING by a previous process (crash recovery).

        Their in-process task died with the old process and can never resume,
        so they are moved to a terminal FAILED state. Returns the count updated.
        """
        now_iso = datetime.now(UTC).isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE status IN (?, ?)",
                (
                    RunStatus.FAILED.value,
                    "interrupted by server restart",
                    now_iso,
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                ),
            )
            await db.commit()
            return cursor.rowcount

    async def delete_profile(self, profile_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM test_profiles WHERE id = ?",
                (profile_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def save_schedule(self, schedule: TestSchedule) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO test_schedules ("
                "id, name, profile_id, cron_expression, enabled, timezone, "
                "last_run_at, next_run_at, created_by, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    schedule.id,
                    schedule.name,
                    schedule.profile_id,
                    schedule.cron_expression,
                    1 if schedule.enabled else 0,
                    schedule.timezone,
                    _dt_to_iso(schedule.last_run_at),
                    _dt_to_iso(schedule.next_run_at),
                    schedule.created_by,
                    _dt_to_iso(schedule.created_at),
                ),
            )
            await db.commit()

    async def claim_schedule_run(self, schedule_id: str, fire_time: datetime) -> bool:
        """Atomically claim a cron fire for leader election across replicas (CONC-2).

        Every replica runs its own in-process scheduler, so a single cron tick
        fires the job once per replica. ``fire_time`` is that tick's scheduled
        time (identical across replicas), so the conditional update advances
        ``last_run_at`` to it only for the first caller; later callers see
        ``last_run_at`` already at/after the tick and lose. Returns True iff this
        caller won and should create the run.
        """
        fire_iso = fire_time.astimezone(UTC).isoformat()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE test_schedules SET last_run_at = ? "
                "WHERE id = ? AND (last_run_at IS NULL OR last_run_at < ?)",
                (fire_iso, schedule_id, fire_iso),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_schedule(self, schedule_id: str) -> TestSchedule | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, name, profile_id, cron_expression, enabled, timezone, "
                "last_run_at, next_run_at, created_by, created_at "
                "FROM test_schedules WHERE id = ?",
                (schedule_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_schedule(row)

    async def list_schedules(self, profile_id: str | None = None) -> list[TestSchedule]:
        async with self._connect() as db:
            if profile_id:
                cursor = await db.execute(
                    "SELECT id, name, profile_id, cron_expression, enabled, timezone, "
                    "last_run_at, next_run_at, created_by, created_at "
                    "FROM test_schedules WHERE profile_id = ? ORDER BY created_at DESC",
                    (profile_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT id, name, profile_id, cron_expression, enabled, timezone, "
                    "last_run_at, next_run_at, created_by, created_at "
                    "FROM test_schedules ORDER BY created_at DESC"
                )
            rows = await cursor.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM test_schedules WHERE id = ?",
                (schedule_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def close(self) -> None:
        """Release the keepalive connection (in-memory DBs only); per-op
        connections are already closed by their context managers."""
        if self._keepalive is not None:
            await self._keepalive.close()
            self._keepalive = None


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(iso: str | None) -> datetime | None:
    if iso is None:
        return None
    return datetime.fromisoformat(iso)


def _row_to_run(row: aiosqlite.Row) -> Run:
    summary_data = json.loads(row[9]) if row[9] else None
    report_data = json.loads(row[10]) if row[10] else None
    env_data = json.loads(row[16]) if len(row) > 16 and row[16] else {}
    locked_val = bool(row[17]) if len(row) > 17 and row[17] else False
    return Run(
        id=row[0],
        status=RunStatus(row[1]),
        runner=row[2],
        created_by=row[3],
        tests_path=row[4],
        args=json.loads(row[5]),
        allure_enabled=bool(row[6]),
        timeout=row[7],
        executor_mode=row[8],
        summary=TestSummary.model_validate(summary_data) if summary_data else None,
        report=ReportRef.model_validate(report_data) if report_data else None,
        exit_code=row[11],
        error=row[12],
        created_at=_iso_to_dt(row[13]),
        started_at=_iso_to_dt(row[14]),
        finished_at=_iso_to_dt(row[15]),
        env=env_data,
        locked=locked_val,
    )


def _row_to_profile(row: aiosqlite.Row) -> TestProfile:
    env_data = json.loads(row[11]) if len(row) > 11 and row[11] else {}
    return TestProfile(
        id=row[0],
        name=row[1],
        description=row[2],
        tests_path=row[3],
        selected_files=json.loads(row[4]),
        selected_markers=json.loads(row[5]),
        extra_args=row[6],
        executor_mode=row[7],
        timeout=row[8],
        created_by=row[9],
        created_at=_iso_to_dt(row[10]),
        env=env_data,
    )


def _row_to_schedule(row: aiosqlite.Row) -> TestSchedule:
    return TestSchedule(
        id=row[0],
        name=row[1],
        profile_id=row[2],
        cron_expression=row[3],
        enabled=bool(row[4]),
        timezone=row[5],
        last_run_at=_iso_to_dt(row[6]),
        next_run_at=_iso_to_dt(row[7]),
        created_by=row[8],
        created_at=_iso_to_dt(row[9]),
    )
