"""Create the isolated M1 greenfield execution catalog."""

from __future__ import annotations

from alembic import op

revision = "m1_greenfield_core"
down_revision = "legacy_baseline"
branch_labels = None
depends_on = None

GREENFIELD_CORE_TABLES = (
    "qep_projects",
    "qep_principals",
    "qep_role_bindings",
    "qep_resource_profiles",
    "qep_suites",
    "qep_suite_revisions",
    "qep_batches",
    "qep_case_manifests",
    "qep_manifest_items",
    "qep_shard_plans",
    "qep_runs",
    "qep_run_manifest_items",
    "qep_workers",
    "qep_worker_generations",
    "qep_assignments",
    "qep_attempts",
    "qep_attempt_events",
    "qep_evidence_index",
    "qep_audit_events",
    "qep_outbox_events",
)

_STATEMENTS = (
    """
    CREATE TABLE qep_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'retired')),
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (name)
    )
    """,
    """
    CREATE TABLE qep_principals (
        id TEXT PRIMARY KEY,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'disabled')),
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (issuer, subject)
    )
    """,
    """
    CREATE TABLE qep_role_bindings (
        principal_id TEXT NOT NULL REFERENCES qep_principals(id),
        role TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        granted_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (principal_id, role, project_id)
    )
    """,
    """
    CREATE TABLE qep_resource_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        profile_version BIGINT NOT NULL CHECK (profile_version > 0),
        framework TEXT NOT NULL,
        requests JSONB NOT NULL,
        limits JSONB NOT NULL,
        internal_workers INTEGER NOT NULL CHECK (internal_workers > 0),
        security_profile_id TEXT NOT NULL,
        approved_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (name, profile_version)
    )
    """,
    """
    CREATE TABLE qep_suites (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'retired')),
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (project_id, name)
    )
    """,
    """
    CREATE TABLE qep_suite_revisions (
        id TEXT PRIMARY KEY,
        suite_id TEXT NOT NULL REFERENCES qep_suites(id),
        revision_no BIGINT NOT NULL CHECK (revision_no > 0),
        source_spec_digest CHAR(64) NOT NULL
            CHECK (source_spec_digest ~ '^[0-9a-f]{64}$'),
        config_digest CHAR(64) NOT NULL
            CHECK (config_digest ~ '^[0-9a-f]{64}$'),
        framework TEXT NOT NULL,
        resource_profile_id TEXT REFERENCES qep_resource_profiles(id),
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'approved', 'retired')),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (suite_id, revision_no)
    )
    """,
    """
    CREATE TABLE qep_batches (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES qep_projects(id),
        suite_revision_id TEXT NOT NULL REFERENCES qep_suite_revisions(id),
        request_digest CHAR(64) NOT NULL
            CHECK (request_digest ~ '^[0-9a-f]{64}$'),
        idempotency_scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'draft'
            CHECK (state IN (
                'draft', 'validating', 'collecting', 'planning',
                'awaiting_admission', 'queued', 'running', 'finalizing',
                'succeeded', 'failed', 'partial', 'cancelled', 'rejected'
            )),
        deadline_at TIMESTAMPTZ,
        priority_class TEXT NOT NULL DEFAULT 'background',
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        write_epoch BIGINT NOT NULL DEFAULT 0 CHECK (write_epoch >= 0),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL,
        UNIQUE (idempotency_scope, idempotency_key)
    )
    """,
    """
    CREATE TABLE qep_case_manifests (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL UNIQUE REFERENCES qep_batches(id),
        schema_version TEXT NOT NULL,
        digest CHAR(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
        item_count BIGINT NOT NULL CHECK (item_count > 0),
        status TEXT NOT NULL DEFAULT 'candidate'
            CHECK (status IN ('candidate', 'approved', 'retired')),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_manifest_items (
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id) ON DELETE CASCADE,
        item_index BIGINT NOT NULL CHECK (item_index >= 0),
        stable_case_id TEXT NOT NULL,
        framework_locator JSONB NOT NULL,
        atomic_group_id TEXT NOT NULL,
        estimated_duration_ms BIGINT NOT NULL CHECK (estimated_duration_ms >= 0),
        resource_profile_id TEXT NOT NULL REFERENCES qep_resource_profiles(id),
        constraints JSONB NOT NULL,
        tags JSONB NOT NULL,
        PRIMARY KEY (manifest_id, item_index),
        UNIQUE (manifest_id, stable_case_id)
    )
    """,
    """
    CREATE TABLE qep_shard_plans (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL UNIQUE REFERENCES qep_batches(id),
        algorithm_version TEXT NOT NULL,
        digest CHAR(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
        run_count BIGINT NOT NULL CHECK (run_count > 0),
        total_estimated_duration_ms BIGINT NOT NULL CHECK (total_estimated_duration_ms >= 0),
        status TEXT NOT NULL DEFAULT 'candidate'
            CHECK (status IN ('candidate', 'approved', 'invalid', 'retired')),
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_runs (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL REFERENCES qep_batches(id),
        plan_id TEXT NOT NULL REFERENCES qep_shard_plans(id),
        shard_index BIGINT NOT NULL CHECK (shard_index >= 0),
        resource_profile_id TEXT NOT NULL REFERENCES qep_resource_profiles(id),
        orchestration_phase TEXT NOT NULL
            CHECK (orchestration_phase IN (
                'planned', 'queued', 'assigned', 'running', 'retry_queued', 'closed'
            )),
        disposition TEXT
            CHECK (disposition IS NULL OR disposition IN (
                'review_required', 'retry_queued', 'closed_no_retry'
            )),
        outcome TEXT
            CHECK (outcome IS NULL OR outcome IN (
                'passed', 'test_failed', 'infra_failed', 'cancelled'
            )),
        current_fence BIGINT NOT NULL DEFAULT 0 CHECK (current_fence >= 0),
        attempt_count BIGINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        run_item_set_digest CHAR(64) NOT NULL CHECK (run_item_set_digest ~ '^[0-9a-f]{64}$'),
        finalization_basis_digest CHAR(64)
            CHECK (
                finalization_basis_digest IS NULL
                OR finalization_basis_digest ~ '^[0-9a-f]{64}$'
            ),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        payload JSONB NOT NULL,
        UNIQUE (plan_id, shard_index),
        CHECK ((orchestration_phase = 'closed') = (outcome IS NOT NULL)),
        CHECK ((disposition = 'closed_no_retry') = (orchestration_phase = 'closed')),
        CHECK ((disposition = 'retry_queued') = (orchestration_phase = 'retry_queued')),
        UNIQUE (id, batch_id)
    )
    """,
    """
    CREATE TABLE qep_run_manifest_items (
        run_id TEXT NOT NULL REFERENCES qep_runs(id) ON DELETE CASCADE,
        manifest_id TEXT NOT NULL REFERENCES qep_case_manifests(id),
        item_index BIGINT NOT NULL,
        PRIMARY KEY (run_id, manifest_id, item_index),
        FOREIGN KEY (manifest_id, item_index)
            REFERENCES qep_manifest_items(manifest_id, item_index),
        UNIQUE (manifest_id, item_index)
    )
    """,
    """
    CREATE TABLE qep_workers (
        id TEXT PRIMARY KEY,
        host_id TEXT NOT NULL,
        current_generation BIGINT NOT NULL CHECK (current_generation > 0),
        status TEXT NOT NULL CHECK (status IN (
            'registering', 'ready', 'busy', 'draining', 'offline', 'quarantined', 'retired'
        )),
        pool_id TEXT NOT NULL,
        last_seen_at TIMESTAMPTZ,
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_worker_generations (
        worker_id TEXT NOT NULL REFERENCES qep_workers(id),
        generation BIGINT NOT NULL CHECK (generation > 0),
        cert_serial TEXT NOT NULL,
        agent_version TEXT NOT NULL,
        capabilities_digest CHAR(64) NOT NULL CHECK (capabilities_digest ~ '^[0-9a-f]{64}$'),
        registered_at TIMESTAMPTZ NOT NULL,
        retired_at TIMESTAMPTZ,
        PRIMARY KEY (worker_id, generation)
    )
    """,
    """
    CREATE TABLE qep_assignments (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES qep_runs(id),
        worker_id TEXT NOT NULL,
        worker_generation BIGINT NOT NULL CHECK (worker_generation > 0),
        spec_digest CHAR(64) NOT NULL CHECK (spec_digest ~ '^[0-9a-f]{64}$'),
        offer_token_hash CHAR(64) NOT NULL CHECK (offer_token_hash ~ '^[0-9a-f]{64}$'),
        state TEXT NOT NULL CHECK (state IN (
            'offered', 'claimed', 'committed', 'expired_prestart',
            'released_prestart', 'cancelled_prestart'
        )),
        offered_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        claimed_at TIMESTAMPTZ,
        committed_at TIMESTAMPTZ,
        attempt_id TEXT,
        fence BIGINT CHECK (fence IS NULL OR fence > 0),
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        payload JSONB NOT NULL,
        FOREIGN KEY (worker_id, worker_generation)
            REFERENCES qep_worker_generations(worker_id, generation),
        CHECK (expires_at > offered_at),
        CHECK ((state = 'committed') = (attempt_id IS NOT NULL AND fence IS NOT NULL))
    )
    """,
    """
    CREATE TABLE qep_attempts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES qep_runs(id),
        attempt_no BIGINT NOT NULL CHECK (attempt_no > 0),
        fence BIGINT NOT NULL CHECK (fence > 0),
        assignment_id TEXT NOT NULL REFERENCES qep_assignments(id),
        worker_id TEXT NOT NULL,
        worker_generation BIGINT NOT NULL CHECK (worker_generation > 0),
        spec_digest CHAR(64) NOT NULL CHECK (spec_digest ~ '^[0-9a-f]{64}$'),
        start_commit_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK (state IN (
            'start_committed', 'provisioning', 'running', 'uploading',
            'passed', 'test_failed', 'infra_failed', 'cancelled', 'attempt_unknown'
        )),
        version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        payload JSONB NOT NULL,
        FOREIGN KEY (worker_id, worker_generation)
            REFERENCES qep_worker_generations(worker_id, generation),
        UNIQUE (run_id, attempt_no),
        UNIQUE (run_id, fence),
        UNIQUE (id, run_id, fence)
    )
    """,
    """
    CREATE TABLE qep_attempt_events (
        attempt_id TEXT NOT NULL REFERENCES qep_attempts(id) ON DELETE CASCADE,
        fence BIGINT NOT NULL CHECK (fence > 0),
        event_id TEXT NOT NULL,
        event_seq BIGINT NOT NULL CHECK (event_seq > 0),
        event_type TEXT NOT NULL,
        payload_digest CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        received_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (attempt_id, event_id),
        UNIQUE (attempt_id, event_seq)
    )
    """,
    """
    CREATE TABLE qep_evidence_index (
        id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL UNIQUE REFERENCES qep_attempts(id),
        assignment_id TEXT NOT NULL,
        fence BIGINT NOT NULL CHECK (fence > 0),
        schema_version TEXT NOT NULL,
        root_digest CHAR(64) NOT NULL CHECK (root_digest ~ '^[0-9a-f]{64}$'),
        object_uri TEXT,
        payload JSONB NOT NULL,
        finalized_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_audit_events (
        id TEXT PRIMARY KEY,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        before_digest CHAR(64)
            CHECK (before_digest IS NULL OR before_digest ~ '^[0-9a-f]{64}$'),
        after_digest CHAR(64)
            CHECK (after_digest IS NULL OR after_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE qep_outbox_events (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_digest CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'leased', 'published', 'quarantined')),
        available_at TIMESTAMPTZ NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        leased_at TIMESTAMPTZ,
        last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _STATEMENTS:
        bind.exec_driver_sql(statement)
    bind.exec_driver_sql(
        """
        CREATE UNIQUE INDEX qep_assignments_one_active_per_run
            ON qep_assignments(run_id)
            WHERE state IN ('offered', 'claimed', 'committed')
        """
    )
    bind.exec_driver_sql(
        """
        CREATE INDEX qep_outbox_pending_idx
            ON qep_outbox_events(available_at, created_at)
            WHERE status IN ('pending', 'leased')
        """
    )
    bind.exec_driver_sql(
        """
        CREATE INDEX qep_runs_batch_phase_idx
            ON qep_runs(batch_id, orchestration_phase, version)
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(GREENFIELD_CORE_TABLES):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
