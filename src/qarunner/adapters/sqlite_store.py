"""Real run store backed by aiosqlite with support for users and authenticated runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite

from qarunner.errors import RunNotFound
from qarunner.models import ReportRef, Run, RunStatus, TestSummary, TestProfile, TestSchedule

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
    """RunStore backed by aiosqlite with WAL mode."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the database, enable WAL, create schemas, and pre-populate admin user."""
        from pathlib import Path

        from qarunner.config import Settings
        from qarunner.core.auth import hash_password

        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

        # Check and alter runs table if created_by is missing
        async with self._db.execute("PRAGMA table_info(runs)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
            if "created_by" not in columns:
                await self._db.execute(
                    "ALTER TABLE runs ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system'"
                )
                await self._db.commit()
            if "executor_mode" not in columns:
                await self._db.execute(
                    "ALTER TABLE runs ADD COLUMN executor_mode TEXT NOT NULL DEFAULT 'subprocess'"
                )
                await self._db.commit()
            if "env_json" not in columns:
                await self._db.execute(
                    "ALTER TABLE runs ADD COLUMN env_json TEXT NOT NULL DEFAULT '{}'"
                )
                await self._db.commit()
            if "locked" not in columns:
                await self._db.execute(
                    "ALTER TABLE runs ADD COLUMN locked INTEGER NOT NULL DEFAULT 0"
                )
                await self._db.commit()

        # Check and alter test_profiles table if env_json is missing
        async with self._db.execute("PRAGMA table_info(test_profiles)") as cursor:
            profile_columns = [row[1] for row in await cursor.fetchall()]
            if "env_json" not in profile_columns:
                await self._db.execute(
                    "ALTER TABLE test_profiles ADD COLUMN env_json TEXT NOT NULL DEFAULT '{}'"
                )
                await self._db.commit()

        # Pre-populate default admin user if no users exist
        async with self._db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            if row and row[0] == 0:
                settings = Settings()
                admin_username = settings.admin_user
                admin_hashed = hash_password(settings.admin_password)
                await self.create_user(admin_username, admin_hashed, "admin")

    async def get_user(self, username: str) -> dict | None:
        """Retrieve a user and their password hash from the database."""
        assert self._db is not None
        cursor = await self._db.execute(
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
        assert self._db is not None
        from datetime import datetime
        now_str = datetime.now(UTC).isoformat()
        await self._db.execute(
            "INSERT INTO users (username, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (username, password_hash, role, now_str),
        )
        await self._db.commit()

    async def list_users(self) -> list[dict]:
        """List all users in the database."""
        assert self._db is not None
        cursor = await self._db.execute(
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
        assert self._db is not None
        await self._db.execute(
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
        await self._db.commit()

    async def get(self, run_id: str) -> Run:
        assert self._db is not None
        cursor = await self._db.execute(
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
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT id, status, runner, created_by, tests_path, args_json, allure_enabled, "
            "timeout, executor_mode, summary_json, report_json, exit_code, error, "
            "created_at, started_at, finished_at, env_json, locked "
            "FROM runs ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [_row_to_run(row) for row in rows]

    async def save_profile(self, profile: TestProfile) -> None:
        assert self._db is not None
        await self._db.execute(
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
        await self._db.commit()

    async def get_profile(self, profile_id: str) -> TestProfile | None:
        assert self._db is not None
        cursor = await self._db.execute(
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
        assert self._db is not None
        if tests_path:
            cursor = await self._db.execute(
                "SELECT id, name, description, tests_path, selected_files, selected_markers, "
                "extra_args, executor_mode, timeout, created_by, created_at, env_json "
                "FROM test_profiles WHERE tests_path = ? ORDER BY created_at DESC",
                (tests_path,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, name, description, tests_path, selected_files, selected_markers, "
                "extra_args, executor_mode, timeout, created_by, created_at, env_json "
                "FROM test_profiles ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [_row_to_profile(row) for row in rows]

    async def lock_run(self, run_id: str, locked: bool) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE runs SET locked = ? WHERE id = ?",
            (1 if locked else 0, run_id),
        )
        await self._db.commit()

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]:
        assert self._db is not None
        from datetime import UTC, timedelta
        cutoff_iso = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        cursor = await self._db.execute(
            "SELECT id, status, runner, created_by, tests_path, args_json, allure_enabled, "
            "timeout, executor_mode, summary_json, report_json, exit_code, error, "
            "created_at, started_at, finished_at, env_json, locked "
            "FROM runs "
            "WHERE finished_at <= ? AND locked = 0 AND status IN ('completed', 'failed', 'timeout')",
            (cutoff_iso,),
        )
        rows = await cursor.fetchall()
        return [_row_to_run(row) for row in rows]

    async def delete_profile(self, profile_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM test_profiles WHERE id = ?",
            (profile_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def save_schedule(self, schedule: TestSchedule) -> None:
        assert self._db is not None
        await self._db.execute(
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
        await self._db.commit()

    async def get_schedule(self, schedule_id: str) -> TestSchedule | None:
        assert self._db is not None
        cursor = await self._db.execute(
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
        assert self._db is not None
        if profile_id:
            cursor = await self._db.execute(
                "SELECT id, name, profile_id, cron_expression, enabled, timezone, "
                "last_run_at, next_run_at, created_by, created_at "
                "FROM test_schedules WHERE profile_id = ? ORDER BY created_at DESC",
                (profile_id,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, name, profile_id, cron_expression, enabled, timezone, "
                "last_run_at, next_run_at, created_by, created_at "
                "FROM test_schedules ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def delete_schedule(self, schedule_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM test_schedules WHERE id = ?",
            (schedule_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


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
