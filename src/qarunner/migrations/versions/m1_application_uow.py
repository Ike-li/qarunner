"""Persist generic versioned facts and their scoped command replays."""

from __future__ import annotations

from alembic import op

revision = "m1_application_uow"
down_revision = "m1_batch_readiness"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = (
    """
    CREATE TABLE qep_versioned_fact_snapshots (
        fact_kind TEXT NOT NULL CHECK (length(fact_kind) > 0),
        fact_key TEXT NOT NULL CHECK (length(fact_key) > 0),
        version BIGINT NOT NULL CHECK (version >= 0),
        fact_id TEXT NOT NULL CHECK (length(fact_id) > 0),
        codec_schema_version TEXT NOT NULL CHECK (length(codec_schema_version) > 0),
        payload_digest CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (fact_kind, fact_key, version),
        CHECK (fact_id = fact_key)
    )
    """,
    """
    CREATE TABLE qep_versioned_fact_commands (
        idempotency_scope TEXT NOT NULL CHECK (length(idempotency_scope) > 0),
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
        request_digest CHAR(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
        command_digest CHAR(64) NOT NULL CHECK (command_digest ~ '^[0-9a-f]{64}$'),
        response_status INTEGER NOT NULL,
        response_ref TEXT NOT NULL,
        fact_kind TEXT NOT NULL,
        fact_key TEXT NOT NULL,
        fact_version BIGINT NOT NULL CHECK (fact_version >= 0),
        fact_payload_digest CHAR(64) NOT NULL
            CHECK (fact_payload_digest ~ '^[0-9a-f]{64}$'),
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (idempotency_scope, idempotency_key),
        FOREIGN KEY (fact_kind, fact_key, fact_version)
            REFERENCES qep_versioned_fact_snapshots(fact_kind, fact_key, version)
    )
    """,
)

_DOWNGRADE_STATEMENTS = (
    "DROP TABLE qep_versioned_fact_commands",
    "DROP TABLE qep_versioned_fact_snapshots",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _UPGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for statement in _DOWNGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)
