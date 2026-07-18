"""Expand the M1 catalog with immutable M0 authority and finalization facts."""

from __future__ import annotations

from alembic import op

revision = "m1_greenfield_facts"
down_revision = "m1_greenfield_core"
branch_labels = None
depends_on = None

GREENFIELD_FACT_TABLES = (
    "qep_suite_retry_policies",
    "qep_platform_retry_policies",
    "qep_unknown_review_policies",
    "qep_duplicate_risk_acceptances",
    "qep_batch_success_policies",
    "qep_batch_cancellation_intents",
    "qep_batch_rejections",
    "qep_preexecution_tasks",
    "qep_batch_preexecution_scope_items",
    "qep_batch_preexecution_closure_bases",
    "qep_materialized_scope_handoffs",
    "qep_unknown_observations",
    "qep_unknown_adjudications",
    "qep_retry_intents",
    "qep_run_item_resolutions",
    "qep_run_finalization_bases",
    "qep_batch_cancellation_scope_items",
    "qep_batch_item_resolutions",
    "qep_batch_finalization_bases",
)

_STATEMENTS = (
    """
    CREATE TABLE qep_suite_retry_policies (
        id TEXT NOT NULL,
        policy_version BIGINT NOT NULL CHECK (policy_version > 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        policy_digest CHAR(64) NOT NULL UNIQUE
            CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
        effective_from TIMESTAMPTZ NOT NULL,
        effective_to TIMESTAMPTZ,
        payload JSONB NOT NULL,
        approved_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id, policy_version),
        CHECK (effective_to IS NULL OR effective_to > effective_from)
    )
    """,
    """
    CREATE TABLE qep_platform_retry_policies (
        id TEXT NOT NULL,
        policy_version BIGINT NOT NULL CHECK (policy_version > 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        policy_digest CHAR(64) NOT NULL UNIQUE
            CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
        effective_from TIMESTAMPTZ NOT NULL,
        effective_to TIMESTAMPTZ,
        payload JSONB NOT NULL,
        approved_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id, policy_version),
        CHECK (effective_to IS NULL OR effective_to > effective_from)
    )
    """,
    """
    CREATE TABLE qep_unknown_review_policies (
        id TEXT NOT NULL,
        policy_version BIGINT NOT NULL CHECK (policy_version > 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        policy_digest CHAR(64) NOT NULL UNIQUE
            CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
        review_sla_seconds BIGINT NOT NULL CHECK (review_sla_seconds > 0),
        payload JSONB NOT NULL,
        approved_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id, policy_version)
    )
    """,
    """
    CREATE TABLE qep_duplicate_risk_acceptances (
        id TEXT NOT NULL,
        acceptance_version BIGINT NOT NULL CHECK (acceptance_version > 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        run_id TEXT NOT NULL REFERENCES qep_runs(id),
        attempt_id TEXT NOT NULL REFERENCES qep_attempts(id),
        fence BIGINT NOT NULL CHECK (fence > 0),
        acceptance_digest CHAR(64) NOT NULL UNIQUE
            CHECK (acceptance_digest ~ '^[0-9a-f]{64}$'),
        valid_from TIMESTAMPTZ NOT NULL,
        valid_until TIMESTAMPTZ NOT NULL,
        consumed_by_retry_decision_digest CHAR(64)
            CHECK (
                consumed_by_retry_decision_digest IS NULL
                OR consumed_by_retry_decision_digest ~ '^[0-9a-f]{64}$'
            ),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id, acceptance_version),
        CHECK (valid_until > valid_from)
    )
    """,
    """
    CREATE TABLE qep_batch_success_policies (
        id TEXT NOT NULL,
        policy_version BIGINT NOT NULL CHECK (policy_version > 0),
        suite_id TEXT NOT NULL REFERENCES qep_suites(id),
        max_test_failed_items BIGINT NOT NULL CHECK (max_test_failed_items >= 0),
        allow_authorized_retry_pass BOOLEAN NOT NULL,
        policy_digest CHAR(64) NOT NULL UNIQUE
            CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        approved_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (id, policy_version)
    )
    """,
    """
    CREATE TABLE qep_batch_cancellation_intents (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        idempotency_key TEXT NOT NULL,
        source TEXT NOT NULL
            CHECK (source IN ('user_request', 'deadline_exceeded', 'policy_enforcement')),
        actor_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        request_digest CHAR(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
        authorization_digest CHAR(64) NOT NULL
            CHECK (authorization_digest ~ '^[0-9a-f]{64}$'),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('pre_plan', 'frozen_plan')),
        preplan_scope_digest CHAR(64)
            CHECK (preplan_scope_digest IS NULL OR preplan_scope_digest ~ '^[0-9a-f]{64}$'),
        manifest_digest CHAR(64)
            CHECK (manifest_digest IS NULL OR manifest_digest ~ '^[0-9a-f]{64}$'),
        shard_plan_version BIGINT CHECK (shard_plan_version IS NULL OR shard_plan_version >= 0),
        shard_plan_digest CHAR(64)
            CHECK (shard_plan_digest IS NULL OR shard_plan_digest ~ '^[0-9a-f]{64}$'),
        canonical_run_set_digest CHAR(64)
            CHECK (
                canonical_run_set_digest IS NULL
                OR canonical_run_set_digest ~ '^[0-9a-f]{64}$'
            ),
        intent_digest CHAR(64) NOT NULL UNIQUE
            CHECK (intent_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_qep_batch_cancel_key UNIQUE (batch_id, idempotency_key),
        CHECK (
            (scope_kind = 'pre_plan'
                AND preplan_scope_digest IS NOT NULL
                AND manifest_digest IS NULL
                AND shard_plan_version IS NULL
                AND shard_plan_digest IS NULL
                AND canonical_run_set_digest IS NULL)
            OR
            (scope_kind = 'frozen_plan'
                AND preplan_scope_digest IS NULL
                AND manifest_digest IS NOT NULL
                AND shard_plan_version IS NOT NULL
                AND shard_plan_digest IS NOT NULL
                AND canonical_run_set_digest IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE qep_batch_rejections (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        stage TEXT NOT NULL
            CHECK (stage IN ('validation', 'collection', 'planning', 'admission')),
        reason_class TEXT NOT NULL CHECK (reason_class IN (
            'invalid_input', 'authorization_denied', 'policy_denied', 'source_failure',
            'integrity_failure', 'planning_failure', 'capacity_rejected'
        )),
        reason_code TEXT NOT NULL,
        input_digest CHAR(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
        authority_digest CHAR(64)
            CHECK (authority_digest IS NULL OR authority_digest ~ '^[0-9a-f]{64}$'),
        rejection_digest CHAR(64) NOT NULL UNIQUE
            CHECK (rejection_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_qep_batch_rejection_source UNIQUE (batch_id, source_batch_version)
    )
    """,
    """
    CREATE TABLE qep_preexecution_tasks (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        phase_ordinal BIGINT NOT NULL CHECK (phase_ordinal >= 0),
        task_kind TEXT NOT NULL,
        task_key TEXT NOT NULL,
        generation BIGINT NOT NULL CHECK (generation > 0),
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        phase_authority_digest CHAR(64) NOT NULL
            CHECK (phase_authority_digest ~ '^[0-9a-f]{64}$'),
        input_digest CHAR(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
        stop_fact_digest CHAR(64)
            CHECK (stop_fact_digest IS NULL OR stop_fact_digest ~ '^[0-9a-f]{64}$'),
        seal_version BIGINT NOT NULL CHECK (seal_version >= 0),
        seal_set_digest CHAR(64) NOT NULL CHECK (seal_set_digest ~ '^[0-9a-f]{64}$'),
        task_digest CHAR(64) NOT NULL UNIQUE CHECK (task_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        stopped_at TIMESTAMPTZ,
        PRIMARY KEY (batch_id, phase_ordinal, task_kind, task_key, generation)
    )
    """,
    """
    CREATE TABLE qep_batch_preexecution_scope_items (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        item_index BIGINT NOT NULL,
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        terminal_kind TEXT NOT NULL CHECK (terminal_kind IN ('rejection', 'prestart_cancel')),
        command_digest CHAR(64) NOT NULL CHECK (command_digest ~ '^[0-9a-f]{64}$'),
        shard_plan_id TEXT NOT NULL REFERENCES qep_shard_plans(id),
        shard_plan_version BIGINT NOT NULL CHECK (shard_plan_version >= 0),
        materialized_run_absence_digest CHAR(64) NOT NULL
            CHECK (materialized_run_absence_digest ~ '^[0-9a-f]{64}$'),
        scope_item_digest CHAR(64) NOT NULL UNIQUE
            CHECK (scope_item_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (batch_id, manifest_id, item_index),
        FOREIGN KEY (manifest_id, item_index)
            REFERENCES qep_manifest_items(manifest_id, item_index)
    )
    """,
    """
    CREATE TABLE qep_batch_preexecution_closure_bases (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL UNIQUE REFERENCES qep_batches(id),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        source_phase TEXT NOT NULL,
        terminal_kind TEXT NOT NULL CHECK (terminal_kind IN ('rejection', 'prestart_cancel')),
        command_digest CHAR(64) NOT NULL CHECK (command_digest ~ '^[0-9a-f]{64}$'),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('pre_plan', 'planned_unmaterialized')),
        materialized_run_absence_digest CHAR(64) NOT NULL
            CHECK (materialized_run_absence_digest ~ '^[0-9a-f]{64}$'),
        execution_absence_snapshot_digest CHAR(64) NOT NULL
            CHECK (execution_absence_snapshot_digest ~ '^[0-9a-f]{64}$'),
        basis_digest CHAR(64) NOT NULL UNIQUE CHECK (basis_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_materialized_scope_handoffs (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL,
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('cancel_intent', 'rejection_conflict')),
        trigger_digest CHAR(64) NOT NULL CHECK (trigger_digest ~ '^[0-9a-f]{64}$'),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        materialized_run_set_digest CHAR(64) NOT NULL
            CHECK (materialized_run_set_digest ~ '^[0-9a-f]{64}$'),
        handoff_digest CHAR(64) NOT NULL UNIQUE CHECK (handoff_digest ~ '^[0-9a-f]{64}$'),
        event_id TEXT NOT NULL UNIQUE,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_qep_handoff_semantic_trigger
            UNIQUE (schema_version, batch_id, trigger_kind, trigger_digest)
    )
    """,
    """
    CREATE TABLE qep_unknown_observations (
        id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL UNIQUE REFERENCES qep_attempts(id),
        observation_digest CHAR(64) NOT NULL UNIQUE
            CHECK (observation_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_unknown_adjudications (
        id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL REFERENCES qep_attempts(id),
        observation_digest CHAR(64) NOT NULL
            CHECK (observation_digest ~ '^[0-9a-f]{64}$'),
        supersedes_adjudication_id TEXT REFERENCES qep_unknown_adjudications(id),
        decision TEXT NOT NULL CHECK (decision IN (
            'confirm_stopped_then_retry', 'accept_duplicate_risk_then_retry',
            'mark_infra_failed_no_retry', 'mark_completed_from_verified_evidence'
        )),
        adjudication_digest CHAR(64) NOT NULL UNIQUE
            CHECK (adjudication_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_retry_intents (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES qep_runs(id),
        source_attempt_id TEXT NOT NULL REFERENCES qep_attempts(id),
        source_attempt_no BIGINT NOT NULL CHECK (source_attempt_no > 0),
        source_fence BIGINT NOT NULL CHECK (source_fence > 0),
        source_run_version BIGINT NOT NULL CHECK (source_run_version >= 0),
        decision_source_kind TEXT NOT NULL CHECK (decision_source_kind IN (
            'suite_policy', 'platform_policy', 'unknown_adjudication'
        )),
        source_item_set_digest CHAR(64) NOT NULL
            CHECK (source_item_set_digest ~ '^[0-9a-f]{64}$'),
        target_item_set_digest CHAR(64) NOT NULL
            CHECK (target_item_set_digest ~ '^[0-9a-f]{64}$'),
        authority_digest CHAR(64) NOT NULL CHECK (authority_digest ~ '^[0-9a-f]{64}$'),
        retry_intent_digest CHAR(64) NOT NULL UNIQUE
            CHECK (retry_intent_digest ~ '^[0-9a-f]{64}$'),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'consumed', 'cancelled')),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_run_item_resolutions (
        run_id TEXT NOT NULL REFERENCES qep_runs(id),
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        item_index BIGINT NOT NULL,
        source_run_version BIGINT NOT NULL CHECK (source_run_version >= 0),
        original_fact_digest CHAR(64) NOT NULL
            CHECK (original_fact_digest ~ '^[0-9a-f]{64}$'),
        effective_fact_digest CHAR(64) NOT NULL
            CHECK (effective_fact_digest ~ '^[0-9a-f]{64}$'),
        unknown_lineage_digest CHAR(64)
            CHECK (unknown_lineage_digest IS NULL OR unknown_lineage_digest ~ '^[0-9a-f]{64}$'),
        aggregation_class TEXT NOT NULL CHECK (aggregation_class IN (
            'passed', 'test_failed', 'infra_failed', 'cancelled', 'unknown_lineage'
        )),
        item_resolution_digest CHAR(64) NOT NULL UNIQUE
            CHECK (item_resolution_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (run_id, manifest_id, item_index),
        FOREIGN KEY (run_id, manifest_id, item_index)
            REFERENCES qep_run_manifest_items(run_id, manifest_id, item_index)
    )
    """,
    """
    CREATE TABLE qep_run_finalization_bases (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE REFERENCES qep_runs(id),
        batch_id TEXT NOT NULL,
        source_run_version BIGINT NOT NULL CHECK (source_run_version >= 0),
        final_attempt_id TEXT REFERENCES qep_attempts(id),
        final_fence BIGINT CHECK (final_fence IS NULL OR final_fence > 0),
        item_resolution_set_digest CHAR(64) NOT NULL
            CHECK (item_resolution_set_digest ~ '^[0-9a-f]{64}$'),
        terminal_input_kind TEXT NOT NULL CHECK (terminal_input_kind IN (
            'verified_evidence', 'verified_cancellation_evidence',
            'prestart_cancel', 'unknown_adjudication'
        )),
        disposition TEXT NOT NULL CHECK (disposition = 'closed_no_retry'),
        outcome TEXT NOT NULL CHECK (outcome IN (
            'passed', 'test_failed', 'infra_failed', 'cancelled'
        )),
        basis_digest CHAR(64) NOT NULL UNIQUE CHECK (basis_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        FOREIGN KEY (run_id, batch_id) REFERENCES qep_runs(id, batch_id),
        CHECK (
            (terminal_input_kind = 'prestart_cancel'
                AND final_attempt_id IS NULL AND final_fence IS NULL)
            OR
            (terminal_input_kind <> 'prestart_cancel'
                AND final_attempt_id IS NOT NULL AND final_fence IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE qep_batch_cancellation_scope_items (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        cancellation_intent_digest CHAR(64) NOT NULL
            CHECK (cancellation_intent_digest ~ '^[0-9a-f]{64}$'),
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        item_index BIGINT NOT NULL,
        resolution_kind TEXT NOT NULL CHECK (resolution_kind IN ('not_executed', 'run_fanout')),
        run_id TEXT REFERENCES qep_runs(id),
        source_run_version BIGINT CHECK (source_run_version IS NULL OR source_run_version >= 0),
        scope_item_digest CHAR(64) NOT NULL UNIQUE
            CHECK (scope_item_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (batch_id, manifest_id, item_index),
        FOREIGN KEY (manifest_id, item_index)
            REFERENCES qep_manifest_items(manifest_id, item_index),
        CHECK (
            (resolution_kind = 'not_executed' AND run_id IS NULL AND source_run_version IS NULL)
            OR
            (resolution_kind = 'run_fanout'
                AND run_id IS NOT NULL
                AND source_run_version IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE qep_batch_item_resolutions (
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        item_index BIGINT NOT NULL,
        source_kind TEXT NOT NULL CHECK (source_kind IN ('run_resolution', 'not_executed')),
        source_run_id TEXT REFERENCES qep_runs(id),
        source_run_basis_digest CHAR(64)
            CHECK (source_run_basis_digest IS NULL OR source_run_basis_digest ~ '^[0-9a-f]{64}$'),
        source_item_resolution_digest CHAR(64)
            CHECK (
                source_item_resolution_digest IS NULL
                OR source_item_resolution_digest ~ '^[0-9a-f]{64}$'
            ),
        not_executed_fact_digest CHAR(64)
            CHECK (
                not_executed_fact_digest IS NULL
                OR not_executed_fact_digest ~ '^[0-9a-f]{64}$'
            ),
        classification TEXT NOT NULL CHECK (classification IN (
            'passed', 'test_failed', 'infra_failed', 'cancelled',
            'unknown_lineage', 'not_executed'
        )),
        resolution_digest CHAR(64) NOT NULL UNIQUE
            CHECK (resolution_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (batch_id, manifest_id, item_index),
        FOREIGN KEY (manifest_id, item_index)
            REFERENCES qep_manifest_items(manifest_id, item_index),
        CHECK (
            (source_kind = 'run_resolution'
                AND source_run_id IS NOT NULL
                AND source_run_basis_digest IS NOT NULL
                AND source_item_resolution_digest IS NOT NULL
                AND not_executed_fact_digest IS NULL)
            OR
            (source_kind = 'not_executed'
                AND source_run_id IS NULL
                AND source_run_basis_digest IS NULL
                AND source_item_resolution_digest IS NULL
                AND not_executed_fact_digest IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE qep_batch_finalization_bases (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL UNIQUE REFERENCES qep_batches(id),
        source_batch_version BIGINT NOT NULL CHECK (source_batch_version >= 0),
        manifest_digest CHAR(64) NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
        shard_plan_digest CHAR(64) NOT NULL CHECK (shard_plan_digest ~ '^[0-9a-f]{64}$'),
        canonical_run_set_digest CHAR(64) NOT NULL
            CHECK (canonical_run_set_digest ~ '^[0-9a-f]{64}$'),
        batch_item_resolution_set_digest CHAR(64) NOT NULL
            CHECK (batch_item_resolution_set_digest ~ '^[0-9a-f]{64}$'),
        original_denominator BIGINT NOT NULL CHECK (original_denominator > 0),
        batch_outcome TEXT NOT NULL CHECK (batch_outcome IN (
            'succeeded', 'failed', 'partial', 'cancelled'
        )),
        basis_digest CHAR(64) NOT NULL UNIQUE CHECK (basis_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _STATEMENTS:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(GREENFIELD_FACT_TABLES):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
