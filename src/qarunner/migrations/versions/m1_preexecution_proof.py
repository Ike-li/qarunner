"""Persist sealed pre-execution task inventory, stop provenance, and planned-scope proof."""

from __future__ import annotations

from alembic import op

revision = "m1_preexecution_proof"
down_revision = "m1_application_uow"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = (
    """
    CREATE TABLE qep_preexecution_task_ledgers (
        batch_id TEXT PRIMARY KEY REFERENCES qep_batches(id),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        ledger_version BIGINT NOT NULL CHECK (ledger_version >= 1),
        high_watermark BIGINT NOT NULL CHECK (high_watermark >= 0),
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_preexecution_task_inventory_seals (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        ledger_version BIGINT NOT NULL CHECK (ledger_version >= 1),
        high_watermark BIGINT NOT NULL CHECK (high_watermark >= 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        task_count BIGINT NOT NULL CHECK (task_count >= 0),
        task_set_digest CHAR(64) NOT NULL CHECK (task_set_digest ~ '^[0-9a-f]{64}$'),
        issuer_id TEXT NOT NULL CHECK (length(issuer_id) > 0),
        sealed_at TIMESTAMPTZ NOT NULL,
        schema_version TEXT NOT NULL CHECK (length(schema_version) > 0),
        seal_digest CHAR(64) NOT NULL UNIQUE CHECK (seal_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        PRIMARY KEY (batch_id, ledger_version, high_watermark)
    )
    """,
    """
    ALTER TABLE qep_preexecution_tasks
        ADD COLUMN task_issuer_id TEXT,
        ADD COLUMN stop_issuer_id TEXT
    """,
    """
    ALTER TABLE qep_preexecution_tasks
        ADD CONSTRAINT preexecution_task_stop_provenance_all_or_none
        CHECK (
            (stop_fact_digest IS NULL) = (stop_issuer_id IS NULL)
            AND (stop_fact_digest IS NULL) = (stopped_at IS NULL)
        )
    """,
    """
    CREATE TABLE qep_preexecution_planned_scope_seals (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        shard_plan_version BIGINT NOT NULL CHECK (shard_plan_version >= 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        manifest_digest CHAR(64) NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
        shard_plan_id TEXT NOT NULL REFERENCES qep_shard_plans(id),
        shard_plan_digest CHAR(64) NOT NULL CHECK (shard_plan_digest ~ '^[0-9a-f]{64}$'),
        item_count BIGINT NOT NULL CHECK (item_count >= 0),
        issuer_id TEXT NOT NULL CHECK (length(issuer_id) > 0),
        sealed_at TIMESTAMPTZ NOT NULL,
        schema_version TEXT NOT NULL CHECK (length(schema_version) > 0),
        seal_digest CHAR(64) NOT NULL UNIQUE CHECK (seal_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        PRIMARY KEY (batch_id, shard_plan_version)
    )
    """,
)

_DOWNGRADE_STATEMENTS = (
    "DROP TABLE qep_preexecution_planned_scope_seals",
    """
    ALTER TABLE qep_preexecution_tasks
        DROP CONSTRAINT preexecution_task_stop_provenance_all_or_none
    """,
    """
    ALTER TABLE qep_preexecution_tasks
        DROP COLUMN task_issuer_id,
        DROP COLUMN stop_issuer_id
    """,
    "DROP TABLE qep_preexecution_task_inventory_seals",
    "DROP TABLE qep_preexecution_task_ledgers",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _UPGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for statement in _DOWNGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)
