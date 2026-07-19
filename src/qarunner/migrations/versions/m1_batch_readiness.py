"""Persist Batch finalization readiness facts and their durable bindings."""

from __future__ import annotations

from alembic import op

revision = "m1_batch_readiness"
down_revision = "m1_greenfield_facts"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = (
    """
    CREATE TABLE qep_batch_finalization_readiness_facts (
        ref TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        batch_version BIGINT NOT NULL CHECK (batch_version = source_batch_version + 1),
        manifest_digest CHAR(64) NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
        shard_plan_digest CHAR(64) NOT NULL CHECK (shard_plan_digest ~ '^[0-9a-f]{64}$'),
        canonical_run_set_digest CHAR(64) NOT NULL
            CHECK (canonical_run_set_digest ~ '^[0-9a-f]{64}$'),
        success_policy_digest CHAR(64) NOT NULL
            CHECK (success_policy_digest ~ '^[0-9a-f]{64}$'),
        batch_cancellation_intent_digest CHAR(64)
            CHECK (
                batch_cancellation_intent_digest IS NULL
                OR batch_cancellation_intent_digest ~ '^[0-9a-f]{64}$'
            ),
        readiness_digest CHAR(64) NOT NULL UNIQUE
            CHECK (readiness_digest ~ '^[0-9a-f]{64}$'),
        authority_digest CHAR(64) NOT NULL CHECK (authority_digest ~ '^[0-9a-f]{64}$'),
        write_epoch BIGINT NOT NULL CHECK (write_epoch > 0),
        compatibility_epoch TEXT NOT NULL CHECK (length(compatibility_epoch) > 0),
        state_model_version BIGINT NOT NULL CHECK (state_model_version > 0),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE (batch_id, source_batch_version),
        UNIQUE (ref, batch_id)
    )
    """,
    """
    ALTER TABLE qep_batches
        ADD COLUMN finalization_readiness_ref TEXT
    """,
    """
    ALTER TABLE qep_batch_finalization_bases
        ADD COLUMN readiness_ref TEXT
    """,
    """
    ALTER TABLE qep_batches
        ADD CONSTRAINT fk_qep_batches_finalization_readiness
        FOREIGN KEY (finalization_readiness_ref, id)
        REFERENCES qep_batch_finalization_readiness_facts(ref, batch_id)
    """,
    """
    ALTER TABLE qep_batch_finalization_bases
        ADD CONSTRAINT fk_qep_batch_basis_readiness
        FOREIGN KEY (readiness_ref, batch_id)
        REFERENCES qep_batch_finalization_readiness_facts(ref, batch_id)
    """,
)

_DOWNGRADE_STATEMENTS = (
    """
    ALTER TABLE qep_batch_finalization_bases
        DROP CONSTRAINT fk_qep_batch_basis_readiness
    """,
    """
    ALTER TABLE qep_batches
        DROP CONSTRAINT fk_qep_batches_finalization_readiness
    """,
    """
    ALTER TABLE qep_batch_finalization_bases
        DROP COLUMN readiness_ref
    """,
    """
    ALTER TABLE qep_batches
        DROP COLUMN finalization_readiness_ref
    """,
    """
    DROP TABLE qep_batch_finalization_readiness_facts
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _UPGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for statement in _DOWNGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)
