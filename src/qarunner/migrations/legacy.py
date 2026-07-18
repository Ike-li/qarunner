"""Frozen v1-v10 legacy PostgreSQL catalog used for baseline adoption."""

from __future__ import annotations

import hashlib

LEGACY_MIGRATIONS: tuple[tuple[int, str], ...] = (
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
    (
        6,
        """
        CREATE TABLE test_schedules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            profile_id TEXT NOT NULL REFERENCES test_profiles(id) ON DELETE CASCADE,
            cron_expression TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
    (
        7,
        """
        CREATE TABLE run_test_cases (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            tests_path TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            suite TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            message TEXT
        );
        CREATE INDEX idx_cases_run ON run_test_cases(run_id);
        CREATE INDEX idx_cases_case
            ON run_test_cases(tests_path, suite, name, created_at)
        """,
    ),
    (
        8,
        """
        CREATE INDEX idx_runs_running_worker
            ON runs(worker_node_id)
            WHERE status = 'running'
        """,
    ),
    (
        9,
        """
        ALTER TABLE runs
            ADD COLUMN cleanup_claimed BOOLEAN NOT NULL DEFAULT FALSE;
        CREATE INDEX idx_runs_cleanup_candidates
            ON runs(finished_at)
            WHERE cleanup_claimed = FALSE
              AND locked = FALSE
              AND status IN ('completed', 'failed', 'timeout')
        """,
    ),
    (
        10,
        """
        CREATE TABLE run_ai_diagnosis (
            run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            diagnosis_json JSONB NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """,
    ),
)

LEGACY_CHECKSUMS = {
    version: hashlib.sha256(ddl.encode("utf-8")).hexdigest() for version, ddl in LEGACY_MIGRATIONS
}

LEGACY_TABLES = frozenset(
    {
        "credentials",
        "run_ai_diagnosis",
        "run_test_cases",
        "runs",
        "schema_migrations",
        "suites",
        "test_profiles",
        "test_schedules",
        "users",
    }
)

# Each entry is (column name, PostgreSQL udt_name, nullable, default expression).
LEGACY_COLUMNS: dict[str, tuple[tuple[str, str, bool, str | None], ...]] = {
    "credentials": (
        ("id", "text", False, None),
        ("name", "text", False, None),
        ("type", "text", False, None),
        ("enc_secret", "text", False, None),
        ("created_by", "text", False, None),
        ("created_at", "timestamptz", False, None),
    ),
    "run_ai_diagnosis": (
        ("run_id", "text", False, None),
        ("diagnosis_json", "jsonb", False, None),
        ("provider", "text", False, None),
        ("model", "text", False, None),
        ("created_at", "timestamptz", False, None),
    ),
    "run_test_cases": (
        ("id", "int8", False, "nextval('run_test_cases_id_seq'::regclass)"),
        ("run_id", "text", False, None),
        ("tests_path", "text", False, None),
        ("created_at", "timestamptz", False, None),
        ("suite", "text", False, None),
        ("name", "text", False, None),
        ("status", "text", False, None),
        ("duration_ms", "int4", False, "0"),
        ("message", "text", True, None),
    ),
    "runs": (
        ("id", "text", False, None),
        ("status", "text", False, None),
        ("runner", "text", False, None),
        ("created_by", "text", False, None),
        ("tests_path", "text", False, None),
        ("args_json", "jsonb", False, "'[]'::jsonb"),
        ("allure_enabled", "bool", False, "true"),
        ("timeout", "int4", True, None),
        ("executor_mode", "text", False, "'docker'::text"),
        ("summary_json", "jsonb", True, None),
        ("report_json", "jsonb", True, None),
        ("exit_code", "int4", True, None),
        ("error", "text", True, None),
        ("created_at", "timestamptz", False, None),
        ("started_at", "timestamptz", True, None),
        ("finished_at", "timestamptz", True, None),
        ("env_json", "jsonb", False, "'{}'::jsonb"),
        ("locked", "bool", False, "false"),
        ("worker_node_id", "text", True, None),
        ("profile_id", "text", True, None),
        ("cleanup_claimed", "bool", False, "false"),
    ),
    "schema_migrations": (
        ("version", "int4", False, None),
        ("checksum", "text", False, None),
        ("applied_at", "timestamptz", False, "now()"),
    ),
    "suites": (
        ("name", "text", False, None),
        ("source", "text", False, "'local'::text"),
        ("repo_url", "text", True, None),
        ("ref", "text", True, None),
        ("credential_ref", "text", True, None),
        ("created_by", "text", False, None),
        ("created_at", "timestamptz", False, None),
    ),
    "test_profiles": (
        ("id", "text", False, None),
        ("name", "text", False, None),
        ("description", "text", True, None),
        ("tests_path", "text", False, None),
        ("runner", "text", False, "'pytest'::text"),
        ("selected_files_json", "jsonb", False, "'[]'::jsonb"),
        ("selected_markers_json", "jsonb", False, "'[]'::jsonb"),
        ("extra_args", "text", False, "''::text"),
        ("executor_mode", "text", False, "'docker'::text"),
        ("timeout", "int4", True, None),
        ("created_by", "text", False, None),
        ("created_at", "timestamptz", False, None),
        ("env_json", "jsonb", False, "'{}'::jsonb"),
        ("webhook_url", "text", True, None),
    ),
    "test_schedules": (
        ("id", "text", False, None),
        ("name", "text", False, None),
        ("profile_id", "text", False, None),
        ("cron_expression", "text", False, None),
        ("enabled", "bool", False, "true"),
        ("timezone", "text", False, "'UTC'::text"),
        ("last_run_at", "timestamptz", True, None),
        ("next_run_at", "timestamptz", True, None),
        ("created_by", "text", False, None),
        ("created_at", "timestamptz", False, None),
    ),
    "users": (
        ("username", "text", False, None),
        ("password_hash", "text", False, None),
        ("role", "text", False, None),
        ("created_at", "timestamptz", False, None),
        ("token_version", "int4", False, "0"),
    ),
}

LEGACY_CONSTRAINTS = (
    ("credentials", "p", "PRIMARY KEY (id)"),
    (
        "run_ai_diagnosis",
        "f",
        "FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE",
    ),
    ("run_ai_diagnosis", "p", "PRIMARY KEY (run_id)"),
    (
        "run_test_cases",
        "f",
        "FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE",
    ),
    ("run_test_cases", "p", "PRIMARY KEY (id)"),
    ("runs", "p", "PRIMARY KEY (id)"),
    ("schema_migrations", "p", "PRIMARY KEY (version)"),
    ("suites", "p", "PRIMARY KEY (name)"),
    ("test_profiles", "p", "PRIMARY KEY (id)"),
    (
        "test_schedules",
        "f",
        "FOREIGN KEY (profile_id) REFERENCES test_profiles(id) ON DELETE CASCADE",
    ),
    ("test_schedules", "p", "PRIMARY KEY (id)"),
    ("users", "p", "PRIMARY KEY (username)"),
)

LEGACY_INDEXES = (
    ("credentials", "credentials_pkey", True, True, "btree", ("id",), 1, None),
    (
        "run_ai_diagnosis",
        "run_ai_diagnosis_pkey",
        True,
        True,
        "btree",
        ("run_id",),
        1,
        None,
    ),
    (
        "run_test_cases",
        "idx_cases_case",
        False,
        False,
        "btree",
        ("tests_path", "suite", "name", "created_at"),
        4,
        None,
    ),
    (
        "run_test_cases",
        "idx_cases_run",
        False,
        False,
        "btree",
        ("run_id",),
        1,
        None,
    ),
    (
        "run_test_cases",
        "run_test_cases_pkey",
        True,
        True,
        "btree",
        ("id",),
        1,
        None,
    ),
    (
        "runs",
        "idx_runs_cleanup_candidates",
        False,
        False,
        "btree",
        ("finished_at",),
        1,
        "cleanup_claimed = false AND locked = false AND "
        "(status = ANY (ARRAY['completed'::text, 'failed'::text, 'timeout'::text]))",
    ),
    (
        "runs",
        "idx_runs_running_worker",
        False,
        False,
        "btree",
        ("worker_node_id",),
        1,
        "status = 'running'::text",
    ),
    ("runs", "runs_pkey", True, True, "btree", ("id",), 1, None),
    (
        "schema_migrations",
        "schema_migrations_pkey",
        True,
        True,
        "btree",
        ("version",),
        1,
        None,
    ),
    ("suites", "suites_pkey", True, True, "btree", ("name",), 1, None),
    (
        "test_profiles",
        "test_profiles_pkey",
        True,
        True,
        "btree",
        ("id",),
        1,
        None,
    ),
    (
        "test_schedules",
        "test_schedules_pkey",
        True,
        True,
        "btree",
        ("id",),
        1,
        None,
    ),
    ("users", "users_pkey", True, True, "btree", ("username",), 1, None),
)
