"""Adopt the exact physical catalog produced by legacy migrations v1-v10."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from qarunner.migrations.legacy import LEGACY_CHECKSUMS, LEGACY_MIGRATIONS

revision = "legacy_baseline"
down_revision = None
branch_labels = None
depends_on = None

_MULTI_STATEMENT_MIGRATIONS = {
    7: (
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
        )
        """,
        "CREATE INDEX idx_cases_run ON run_test_cases(run_id)",
        """
        CREATE INDEX idx_cases_case
            ON run_test_cases(tests_path, suite, name, created_at)
        """,
    ),
    9: (
        """
        ALTER TABLE runs
            ADD COLUMN cleanup_claimed BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        CREATE INDEX idx_runs_cleanup_candidates
            ON runs(finished_at)
            WHERE cleanup_claimed = FALSE
              AND locked = FALSE
              AND status IN ('completed', 'failed', 'timeout')
        """,
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for version, ddl in LEGACY_MIGRATIONS:
        for statement in _MULTI_STATEMENT_MIGRATIONS.get(version, (ddl,)):
            bind.exec_driver_sql(statement)
    for version, _ddl in LEGACY_MIGRATIONS:
        bind.execute(
            sa.text(
                "INSERT INTO schema_migrations (version, checksum) VALUES (:version, :checksum)"
            ),
            {"version": version, "checksum": LEGACY_CHECKSUMS[version]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "run_ai_diagnosis",
        "run_test_cases",
        "test_schedules",
        "test_profiles",
        "credentials",
        "suites",
        "runs",
        "users",
        "schema_migrations",
    ):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
