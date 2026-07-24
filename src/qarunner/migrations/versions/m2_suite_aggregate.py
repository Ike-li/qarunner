"""Expand qep_suites for M2 Suite CAS (version + retired_at).

M2 SUITE-PERSIST needs optimistic version CAS and durable retire timestamps.
Existing seed paths omit these columns (DEFAULT 0 / NULL) so they stay compatible.
"""

from __future__ import annotations

from alembic import op

revision = "m2_suite_aggregate"
down_revision = "m1_preexecution_proof"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = (
    """
    ALTER TABLE qep_suites
        ADD COLUMN version BIGINT NOT NULL DEFAULT 0
            CHECK (version >= 0)
    """,
    """
    ALTER TABLE qep_suites
        ADD COLUMN retired_at TIMESTAMPTZ
    """,
    """
    ALTER TABLE qep_suites
        ADD CONSTRAINT qep_suites_retired_at_consistency
        CHECK (
            (status = 'retired' AND retired_at IS NOT NULL)
            OR (status = 'active' AND retired_at IS NULL)
        )
    """,
)

_DOWNGRADE_STATEMENTS = (
    "ALTER TABLE qep_suites DROP CONSTRAINT IF EXISTS qep_suites_retired_at_consistency",
    "ALTER TABLE qep_suites DROP COLUMN IF EXISTS retired_at",
    "ALTER TABLE qep_suites DROP COLUMN IF EXISTS version",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _UPGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for statement in _DOWNGRADE_STATEMENTS:
        bind.exec_driver_sql(statement)
