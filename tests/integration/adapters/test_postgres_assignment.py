"""ASGN-OFFER: real PostgreSQL concurrent Assignment offer CAS.

8 contenders race on one QUEUED run via asyncio.Barrier; exactly one writes an
`offered` row and bumps the Run to `assigned`/`version+1`. The rest surface
`VersionConflict`. Proves `T-M1-CLAIM-001` core via
`qep_assignments_one_active_per_run` + run-version CAS under real contention.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_assignment import PostgresAssignmentGateway
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.ports.common import PortContractError
from qarunner.domain import (
    AssignmentConflict,
    Digest,
    IdempotencyConflict,
    Run,
    VersionConflict,
    WorkerAuthority,
    WorkerGeneration,
    WorkerRef,
    WorkerState,
    canonical_digest,
)
from qarunner.domain.run import CommitStartResult, RunState

OFFER_TOKEN_HASH = "d" * 64
SPEC_DIGEST = canonical_digest(
    schema_version="qep.test-assignment-offer.v1",
    payload={"label": "execution-spec"},
)
OFFERED_AT = datetime(2026, 7, 23, 12, tzinfo=UTC)
EXPIRES_AT = OFFERED_AT + timedelta(hours=1)
WORKER_ID = "worker-001"
WORKER_GENERATION = 3
RUN_ID = "run-offer-1"
BATCH_ID = "batch-offer-1"


@pytest.fixture
async def assignment_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-assignment-admin-password-at-least-32-chars",
    )
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    migration = await asyncio.to_thread(
        run_migration_operator,
        database_url,
        schema,
        "upgrade",
        "head",
    )
    assert migration.returncode == 0, migration.stderr
    store = PostgresStore(database_url, schema=schema)
    try:
        await store.initialize()
        yield store
    finally:
        await store.close()
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


def _ready_worker():
    registered_at = datetime(2026, 7, 12, 12, tzinfo=UTC)
    worker = WorkerGeneration.register(
        ref=WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION),
        host_id="host-001",
        pool_id="pool-default",
        cert_serial="cert-3",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.test-capabilities.v1",
            payload={"label": "capabilities-3"},
        ),
        registered_at=registered_at,
    )
    authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=0,
        occurred_at=registered_at + timedelta(seconds=1),
    )
    return ready, authority


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


async def _seed_queued_run(pool: asyncpg.Pool) -> None:
    """Project/profile/suite/revision/batch/manifest/plan/run + one worker fixture.

    Run is QUEUED, current_fence=0, no assignment/attempt — the offer precondition.
    """
    now = OFFERED_AT
    empty = json.dumps({})
    caps = _digest_hex(
        canonical_digest(
            schema_version="qep.test-capabilities.v1",
            payload={"label": "capabilities-3"},
        )
    )
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO qep_projects (id, name, created_at) VALUES ($1, $2, $3)",
            "project-1",
            "Project 1",
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_resource_profiles (
                id, name, profile_version, framework, requests, limits,
                internal_workers, security_profile_id, approved_at, created_at
            ) VALUES ($1, $2, 1, 'pytest', $3, $3, 1, $4, $5, $5)
            """,
            "profile-1",
            "Default",
            empty,
            "security-profile-1",
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_suites (id, project_id, name, created_at)
            VALUES ($1, $2, $3, $4)
            """,
            "suite-1",
            "project-1",
            "Suite 1",
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_suite_revisions (
                id, suite_id, revision_no, source_spec_digest, config_digest,
                framework, resource_profile_id, status, payload, created_at
            ) VALUES ($1, $2, 1, $3, $4, 'pytest', $5, 'approved', $6, $7)
            """,
            "suite-revision-1",
            "suite-1",
            _digest_hex(SPEC_DIGEST),
            "a" * 64,
            "profile-1",
            empty,
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_batches (
                id, project_id, suite_revision_id, request_digest, idempotency_scope,
                idempotency_key, state, version, write_epoch, created_at, updated_at, payload
            ) VALUES ($1, $2, $3, $4, $5, $6, 'running', 0, 1, $7, $7, $8)
            """,
            BATCH_ID,
            "project-1",
            "suite-revision-1",
            "b" * 64,
            "batch:create",
            "batch-offer-1",
            now,
            empty,
        )
        await connection.execute(
            """
            INSERT INTO qep_case_manifests (
                id, batch_id, schema_version, digest, item_count, status, payload, created_at
            ) VALUES ($1, $2, $3, $4, 1, 'approved', $5, $6)
            """,
            "manifest-1",
            BATCH_ID,
            "qep.case-manifest.v1",
            "c" * 64,
            empty,
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_manifest_items (
                manifest_id, item_index, stable_case_id, framework_locator, atomic_group_id,
                estimated_duration_ms, resource_profile_id, constraints, tags
            ) VALUES ($1, 0, $2, $3, $2, 1, $4, $3, $3)
            """,
            "manifest-1",
            "case-1",
            empty,
            "profile-1",
        )
        await connection.execute(
            """
            INSERT INTO qep_shard_plans (
                id, batch_id, algorithm_version, digest, run_count,
                total_estimated_duration_ms, status, payload, created_at
            ) VALUES ($1, $2, 'single-shard.v1', $3, 1, 1, 'approved', $4, $5)
            """,
            "plan-1",
            BATCH_ID,
            "e" * 64,
            empty,
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_runs (
                id, batch_id, plan_id, shard_index, resource_profile_id,
                orchestration_phase, current_fence, attempt_count, version,
                run_item_set_digest, created_at, updated_at, payload
            ) VALUES ($1, $2, $3, 0, $4, 'queued', 0, 0, 0, $5, $6, $6, $7)
            """,
            RUN_ID,
            BATCH_ID,
            "plan-1",
            "profile-1",
            "f" * 64,
            now,
            empty,
        )
        await connection.execute(
            """
            INSERT INTO qep_run_manifest_items (run_id, manifest_id, item_index)
            VALUES ($1, $2, 0)
            """,
            RUN_ID,
            "manifest-1",
        )
        await connection.execute(
            """
            INSERT INTO qep_workers (
                id, host_id, current_generation, status, pool_id,
                last_seen_at, version, created_at
            ) VALUES ($1, $2, $3, 'ready', $4, $5, 0, $5)
            """,
            WORKER_ID,
            "host-001",
            WORKER_GENERATION,
            "pool-default",
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_worker_generations (
                worker_id, generation, cert_serial, agent_version,
                capabilities_digest, registered_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            WORKER_ID,
            WORKER_GENERATION,
            "cert-3",
            "1.0.0",
            caps,
            now,
        )


async def _offer_once(
    pool: asyncpg.Pool, *, assignment_id: str, expected_version: int | None = None
) -> Run:
    worker, authority = _ready_worker()
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        version = snapshot.version if expected_version is None else expected_version
        offered = snapshot.run.offer_assignment(
            assignment_id=assignment_id,
            worker=worker,
            worker_authority=authority,
            spec_digest=SPEC_DIGEST,
            offered_at=OFFERED_AT,
            expires_at=EXPIRES_AT,
            expected_version=version,
        )
        result = await gateway.publish_offer(offered=offered, expected=snapshot)
    assert result.replayed is False
    return result.value


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_offer_token_hash_fixture(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    with pytest.raises(PortContractError) as short:
        PostgresAssignmentGateway(pool, offer_token_hash="not-a-digest")
    assert short.value.field == "offer_token_hash"
    with pytest.raises(PortContractError) as non_hex:
        PostgresAssignmentGateway(pool, offer_token_hash="g" * 64)
    assert non_hex.value.field == "offer_token_hash"


@pytest.mark.asyncio
async def test_single_offer_writes_one_assignment_and_assigns_the_run(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)

    offered = await _offer_once(pool, assignment_id="assignment-001")

    assert offered.state is RunState.ASSIGNED
    assert offered.version == 1
    assert offered.current_assignment_id == "assignment-001"
    async with pool.acquire() as connection:
        assignment = await connection.fetchrow(
            "SELECT id, state, worker_id, worker_generation, attempt_id, fence, version "
            "FROM qep_assignments"
        )
        run = await connection.fetchrow(
            "SELECT orchestration_phase, version, current_fence FROM qep_runs WHERE id = $1",
            RUN_ID,
        )
    assert dict(assignment) == {
        "id": "assignment-001",
        "state": "offered",
        "worker_id": WORKER_ID,
        "worker_generation": WORKER_GENERATION,
        "attempt_id": None,
        "fence": None,
        "version": 0,
    }
    assert dict(run) == {
        "orchestration_phase": "assigned",
        "version": 1,
        "current_fence": 0,
    }


@pytest.mark.asyncio
async def test_eight_concurrent_offers_elect_exactly_one_winner(
    assignment_store: PostgresStore,
) -> None:
    """ASGN-OFFER core: 8-contender Barrier → 1 offered row + 7 VersionConflict."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    contenders = 8
    start = asyncio.Barrier(contenders)
    worker, authority = _ready_worker()

    async def contend(index: int):
        await start.wait()
        try:
            async with PostgresAssignmentGateway(
                pool, offer_token_hash=OFFER_TOKEN_HASH
            ) as gateway:
                snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
                offered = snapshot.run.offer_assignment(
                    assignment_id=f"assignment-{index:03d}",
                    worker=worker,
                    worker_authority=authority,
                    spec_digest=SPEC_DIGEST,
                    offered_at=OFFERED_AT,
                    expires_at=EXPIRES_AT,
                    expected_version=snapshot.version,
                )
                return await gateway.publish_offer(offered=offered, expected=snapshot)
        except BaseException as error:
            return error

    results = await asyncio.gather(*(contend(i) for i in range(contenders)))

    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, VersionConflict)]
    # Domain may also surface AssignmentConflict if a contender rehydrates mid-race
    # after the winner committed (defensive; primary path is VersionConflict).
    other_errors = [
        result
        for result in results
        if isinstance(result, BaseException) and not isinstance(result, VersionConflict)
    ]
    assert len(winners) == 1, results
    assert len(conflicts) + len(other_errors) == contenders - 1
    assert all(isinstance(err, (VersionConflict, AssignmentConflict)) for err in other_errors) or (
        len(other_errors) == 0
    )
    winner = winners[0]
    assert winner.replayed is False
    assert winner.value.state is RunState.ASSIGNED
    assert winner.value.version == 1

    async with pool.acquire() as connection:
        assignment_count = await connection.fetchval("SELECT count(*) FROM qep_assignments")
        assignment_states = await connection.fetch("SELECT id, state FROM qep_assignments")
        run = await connection.fetchrow(
            "SELECT orchestration_phase, version FROM qep_runs WHERE id = $1",
            RUN_ID,
        )
    assert assignment_count == 1
    assert len(assignment_states) == 1
    assert assignment_states[0]["state"] == "offered"
    assert dict(run) == {"orchestration_phase": "assigned", "version": 1}


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_unknown_run(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id="missing-run")
    assert exc.value.reason == "not_found"


@pytest.mark.asyncio
async def test_get_run_for_update_rehydrates_offered_assignment(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)

    assert snapshot.run.state is RunState.ASSIGNED
    assert snapshot.run.version == 1
    assert snapshot.run.current_assignment_id == "assignment-001"
    assert snapshot.run.assignment is not None
    assert snapshot.run.assignment.id == "assignment-001"
    assert snapshot.run.assignment.state.value == "offered"
    assert snapshot.run.assignment.worker.worker_id == WORKER_ID
    assert snapshot.run.assignment.worker.generation == WORKER_GENERATION


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_closed_phase(
    assignment_store: PostgresStore,
) -> None:
    """Closed phase is outside offer/claim/commit-start and surfaces as VersionConflict."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = 'closed',
                disposition = 'closed_no_retry',
                outcome = 'passed',
                version = 5
            WHERE id = $1
            """,
            RUN_ID,
        )
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id=RUN_ID)


@pytest.mark.asyncio
async def test_repeated_get_run_for_update_returns_same_snapshot(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        first = await gateway.get_run_for_update(run_id=RUN_ID)
        second = await gateway.get_run_for_update(run_id=RUN_ID)
    assert first is second


@pytest.mark.asyncio
async def test_publish_offer_rejects_stale_snapshot(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    worker, authority = _ready_worker()
    draft = Run.create(run_id=RUN_ID)
    queued = draft.transition(RunState.QUEUED, expected_version=0)
    from qarunner.application.ports.assignment import AssignmentMutationSnapshot

    stale = AssignmentMutationSnapshot(run=draft)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            offered = snapshot.run.offer_assignment(
                assignment_id="assignment-stale",
                worker=worker,
                worker_authority=authority,
                spec_digest=SPEC_DIGEST,
                offered_at=OFFERED_AT,
                expires_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            await gateway.publish_offer(offered=offered, expected=stale)
    # Stale path aborts; no durable write.
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_assignments") == 0
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "queued"
        )
    assert queued.state is RunState.QUEUED


@pytest.mark.asyncio
async def test_publish_offer_rejects_run_id_mismatch(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    worker, authority = _ready_worker()
    other = (
        Run.create(run_id="other-run")
        .transition(RunState.QUEUED, expected_version=0)
        .offer_assignment(
            assignment_id="assignment-other",
            worker=worker,
            worker_authority=authority,
            spec_digest=SPEC_DIGEST,
            offered_at=OFFERED_AT,
            expires_at=EXPIRES_AT,
            expected_version=1,
        )
    )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_offer(offered=other, expected=snapshot)
    assert exc.value.reason == "run_id_mismatch"


@pytest.mark.asyncio
async def test_publish_offer_rejects_non_assigned_domain_result(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_offer(offered=snapshot.run, expected=snapshot)
    assert exc.value.reason == "not_assigned"


@pytest.mark.asyncio
async def test_publish_offer_version_cas_conflict_after_in_tx_version_bump(
    assignment_store: PostgresStore,
) -> None:
    """Force UPDATE 0 by bumping version on the same connection after FOR UPDATE."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    worker, authority = _ready_worker()
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            offered = snapshot.run.offer_assignment(
                assignment_id="assignment-cas",
                worker=worker,
                worker_authority=authority,
                spec_digest=SPEC_DIGEST,
                offered_at=OFFERED_AT,
                expires_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_runs SET version = version + 10 WHERE id = $1",
                RUN_ID,
            )
            await gateway.publish_offer(offered=offered, expected=snapshot)
    async with pool.acquire() as connection:
        # Sticky-abort rolls the whole UoW back, including the in-tx version bump.
        assert await connection.fetchval("SELECT count(*) FROM qep_assignments") == 0
        assert await connection.fetchval("SELECT version FROM qep_runs WHERE id = $1", RUN_ID) == 0


@pytest.mark.asyncio
async def test_suppressed_assignment_insert_sticky_aborts(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    worker, authority = _ready_worker()
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_assignment_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_assignment_insert
            BEFORE INSERT ON qep_assignments
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_assignment_insert()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            offered = snapshot.run.offer_assignment(
                assignment_id="assignment-suppressed",
                worker=worker,
                worker_authority=authority,
                spec_digest=SPEC_DIGEST,
                offered_at=OFFERED_AT,
                expires_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            await gateway.publish_offer(offered=offered, expected=snapshot)
    assert exc.value.reason == "insert_suppressed"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_assignments") == 0
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "queued"
        )


async def _claim_once(
    pool: asyncpg.Pool,
    *,
    assignment_id: str,
    observed_at: datetime | None = None,
    worker_ref: WorkerRef | None = None,
) -> Run:
    observed = observed_at if observed_at is not None else OFFERED_AT + timedelta(minutes=1)
    worker = (
        worker_ref
        if worker_ref is not None
        else WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    )
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        claimed = snapshot.run.claim_assignment(
            assignment_id=assignment_id,
            worker=worker,
            observed_at=observed,
            expected_version=snapshot.version,
        )
        result = await gateway.publish_claim(claimed=claimed, expected=snapshot)
    assert result.replayed is False
    return result.value


@pytest.mark.asyncio
async def test_single_claim_transitions_assignment_to_claimed(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")

    claimed = await _claim_once(pool, assignment_id="assignment-001")

    assert claimed.state is RunState.ASSIGNED
    assert claimed.version == 2
    assert claimed.assignment is not None
    assert claimed.assignment.state.value == "claimed"
    assert claimed.assignment.claimed_at == OFFERED_AT + timedelta(minutes=1)
    async with pool.acquire() as connection:
        assignment = await connection.fetchrow(
            "SELECT state, claimed_at, version, attempt_id, fence FROM qep_assignments"
        )
        run = await connection.fetchrow(
            "SELECT orchestration_phase, version FROM qep_runs WHERE id = $1",
            RUN_ID,
        )
    assert dict(assignment) == {
        "state": "claimed",
        "claimed_at": OFFERED_AT + timedelta(minutes=1),
        "version": 1,
        "attempt_id": None,
        "fence": None,
    }
    assert dict(run) == {"orchestration_phase": "assigned", "version": 2}


@pytest.mark.asyncio
async def test_eight_concurrent_claims_elect_exactly_one_winner(
    assignment_store: PostgresStore,
) -> None:
    """ASGN-CLAIM core: 8-contender Barrier on one offered assignment → 1 claimed."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    contenders = 8
    start = asyncio.Barrier(contenders)
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    observed = OFFERED_AT + timedelta(minutes=1)

    async def contend():
        await start.wait()
        try:
            async with PostgresAssignmentGateway(
                pool, offer_token_hash=OFFER_TOKEN_HASH
            ) as gateway:
                snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
                claimed = snapshot.run.claim_assignment(
                    assignment_id="assignment-001",
                    worker=worker,
                    observed_at=observed,
                    expected_version=snapshot.version,
                )
                return await gateway.publish_claim(claimed=claimed, expected=snapshot)
        except BaseException as error:
            return error

    results = await asyncio.gather(*(contend() for _ in range(contenders)))
    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [
        result for result in results if isinstance(result, (VersionConflict, AssignmentConflict))
    ]
    assert len(winners) == 1, results
    assert len(conflicts) == contenders - 1
    assert winners[0].value.assignment is not None
    assert winners[0].value.assignment.state.value == "claimed"
    assert winners[0].value.version == 2

    async with pool.acquire() as connection:
        assignment = await connection.fetchrow(
            "SELECT state, version, count(*) OVER () AS total FROM qep_assignments"
        )
        run_version = await connection.fetchval(
            "SELECT version FROM qep_runs WHERE id = $1", RUN_ID
        )
    assert assignment["state"] == "claimed"
    assert assignment["version"] == 1
    assert assignment["total"] == 1
    assert run_version == 2


@pytest.mark.asyncio
async def test_claim_rejects_wrong_worker(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(AssignmentConflict) as exc:
        await _claim_once(
            pool,
            assignment_id="assignment-001",
            worker_ref=WorkerRef(worker_id=WORKER_ID, generation=99),
        )
    assert exc.value.reason == "offer_mismatch"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT state FROM qep_assignments") == "offered"


@pytest.mark.asyncio
async def test_claim_rejects_expired_offer(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(AssignmentConflict) as exc:
        await _claim_once(
            pool,
            assignment_id="assignment-001",
            observed_at=EXPIRES_AT,
        )
    assert exc.value.reason == "assignment_expired"


@pytest.mark.asyncio
async def test_claim_rejects_stale_run_version(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            claimed = snapshot.run.claim_assignment(
                assignment_id="assignment-001",
                worker=worker,
                observed_at=OFFERED_AT + timedelta(minutes=1),
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_runs SET version = version + 10 WHERE id = $1",
                RUN_ID,
            )
            await gateway.publish_claim(claimed=claimed, expected=snapshot)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT state FROM qep_assignments") == "offered"
        assert await connection.fetchval("SELECT version FROM qep_runs WHERE id = $1", RUN_ID) == 1


@pytest.mark.asyncio
async def test_claim_rejects_already_claimed_assignment_via_cas(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    with pytest.raises((VersionConflict, AssignmentConflict)):
        await _claim_once(pool, assignment_id="assignment-001")


@pytest.mark.asyncio
async def test_publish_claim_rejects_run_id_mismatch(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            claimed = snapshot.run.claim_assignment(
                assignment_id="assignment-001",
                worker=worker,
                observed_at=OFFERED_AT + timedelta(minutes=1),
                expected_version=snapshot.version,
            )
            # Forge a different run id after domain transition.
            from dataclasses import replace

            forged = replace(claimed, id="other-run")
            await gateway.publish_claim(claimed=forged, expected=snapshot)
    assert exc.value.reason == "run_id_mismatch"


@pytest.mark.asyncio
async def test_publish_claim_rejects_non_claimed_domain_result(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            # Still OFFERED — publish_claim requires CLAIMED domain result.
            await gateway.publish_claim(claimed=snapshot.run, expected=snapshot)
    assert exc.value.reason == "not_claimed"


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_assigned_without_active_assignment(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_runs SET orchestration_phase = 'assigned', version = 1 WHERE id = $1",
            RUN_ID,
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id=RUN_ID)
    assert exc.value.reason == "active_assignment_missing"


@pytest.mark.asyncio
async def test_publish_claim_rejects_stale_snapshot(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    from qarunner.application.ports.assignment import AssignmentMutationSnapshot

    draft = Run.create(run_id=RUN_ID)
    stale = AssignmentMutationSnapshot(run=draft)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            claimed = snapshot.run.claim_assignment(
                assignment_id="assignment-001",
                worker=worker,
                observed_at=OFFERED_AT + timedelta(minutes=1),
                expected_version=snapshot.version,
            )
            await gateway.publish_claim(claimed=claimed, expected=stale)


@pytest.mark.asyncio
async def test_publish_claim_rejects_non_assigned_domain_result(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    bogus = SimpleNamespace(id=RUN_ID, state=RunState.QUEUED, assignment=None)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_claim(claimed=bogus, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "not_assigned"


@pytest.mark.asyncio
async def test_publish_claim_rejects_assigned_missing_assignment(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    bogus = SimpleNamespace(id=RUN_ID, state=RunState.ASSIGNED, assignment=None)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_claim(claimed=bogus, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "missing_assignment"


@pytest.mark.asyncio
async def test_publish_claim_rejects_claimed_without_claimed_at(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    from qarunner.domain.assignment import AssignmentState

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    fake_assignment = SimpleNamespace(
        id="assignment-001",
        state=AssignmentState.CLAIMED,
        claimed_at=None,
        worker=WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION),
        spec_digest=SPEC_DIGEST,
        retry_intent_id=None,
    )
    bogus = SimpleNamespace(id=RUN_ID, state=RunState.ASSIGNED, assignment=fake_assignment)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_claim(claimed=bogus, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "missing_claimed_at"


@pytest.mark.asyncio
async def test_publish_claim_rejects_when_assignment_row_already_claimed(
    assignment_store: PostgresStore,
) -> None:
    """Force assignment-row CAS miss after FOR UPDATE (in-tx version bump)."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            claimed = snapshot.run.claim_assignment(
                assignment_id="assignment-001",
                worker=worker,
                observed_at=OFFERED_AT + timedelta(minutes=1),
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_assignments SET version = version + 10 WHERE id = $1",
                "assignment-001",
            )
            await gateway.publish_claim(claimed=claimed, expected=snapshot)


@pytest.mark.asyncio
async def test_expire_precommit_closes_assignment_and_returns_run_to_queued(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT,
            expected_version=snapshot.version,
        )
        result = await gateway.publish_close(closed=closed, expected=snapshot)

    assert result.replayed is False
    assert result.value.state is RunState.QUEUED
    assert result.value.version == 2
    assert result.value.current_assignment_id is None
    assert result.value.current_fence == 0
    historical = result.value.assignments[-1]
    assert historical.state.value == "expired_prestart"
    assert historical.closure is not None
    assert historical.closure.idempotency_key == "expiry-001"

    async with pool.acquire() as connection:
        assignment = await connection.fetchrow(
            "SELECT state, version FROM qep_assignments WHERE id = $1",
            "assignment-001",
        )
        run = await connection.fetchrow(
            "SELECT orchestration_phase, version, current_fence FROM qep_runs WHERE id = $1",
            RUN_ID,
        )
    assert dict(assignment) == {"state": "expired_prestart", "version": 1}
    assert dict(run) == {
        "orchestration_phase": "queued",
        "version": 2,
        "current_fence": 0,
    }


@pytest.mark.asyncio
async def test_release_precommit_closes_claimed_assignment(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker,
            release_key="release-001",
            observed_at=OFFERED_AT + timedelta(minutes=2),
            expected_version=snapshot.version,
        )
        result = await gateway.publish_close(closed=closed, expected=snapshot)

    assert result.replayed is False
    assert result.value.state is RunState.QUEUED
    assert result.value.version == 3
    assert result.value.assignments[-1].state.value == "released_prestart"
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT state FROM qep_assignments WHERE id = $1", "assignment-001"
            )
            == "released_prestart"
        )
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "queued"
        )


@pytest.mark.asyncio
async def test_exact_close_key_replays_without_duplicate_write(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT,
            expected_version=snapshot.version,
        )
        first = await gateway.publish_close(closed=closed, expected=snapshot)
    assert first.replayed is False

    # Domain exact-replay (same key) after durable close — no second write.
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        replay_domain = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT + timedelta(seconds=1),
            expected_version=0,
        )
        second = await gateway.publish_close(closed=replay_domain, expected=snapshot)

    assert second.replayed is True
    assert second.value.version == snapshot.version
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_assignments") == 1
        assert await connection.fetchval("SELECT version FROM qep_runs WHERE id = $1", RUN_ID) == 2


@pytest.mark.asyncio
async def test_closed_assignment_id_cannot_be_reused_on_fresh_offer(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT,
            expected_version=snapshot.version,
        )
        await gateway.publish_close(closed=closed, expected=snapshot)

    with pytest.raises(AssignmentConflict) as exc:
        await _offer_once(pool, assignment_id="assignment-001")
    assert exc.value.reason == "assignment_id_reused"


@pytest.mark.asyncio
async def test_after_close_fresh_offer_first_commit_is_still_fence_1(
    assignment_store: PostgresStore,
) -> None:
    """ASGN-CLOSE core: reassignment after prestart close keeps fence monotonic from 0→1."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT,
            expected_version=snapshot.version,
        )
        await gateway.publish_close(closed=closed, expected=snapshot)

    # Fresh offer must be at/after the prior closure recorded_at (domain timeline).
    worker, authority = _ready_worker()
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        offered = snapshot.run.offer_assignment(
            assignment_id="assignment-002",
            worker=worker,
            worker_authority=authority,
            spec_digest=SPEC_DIGEST,
            offered_at=EXPIRES_AT,
            expires_at=EXPIRES_AT + timedelta(hours=1),
            expected_version=snapshot.version,
        )
        await gateway.publish_offer(offered=offered, expected=snapshot)

    worker_ref = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        claimed = snapshot.run.claim_assignment(
            assignment_id="assignment-002",
            worker=worker_ref,
            observed_at=EXPIRES_AT + timedelta(minutes=1),
            expected_version=snapshot.version,
        )
        await gateway.publish_claim(claimed=claimed, expected=snapshot)

    commit = await _commit_start_once(
        pool,
        assignment_id="assignment-002",
        start_commit_key="start-commit-002",
        new_attempt_id="attempt-002",
        observed_at=EXPIRES_AT + timedelta(minutes=2),
    )
    assert commit.fence == 1
    assert commit.run.current_fence == 1
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_assignments") == 2
        assert await connection.fetchval("SELECT count(*) FROM qep_attempts") == 1
        assert (
            await connection.fetchval("SELECT current_fence FROM qep_runs WHERE id = $1", RUN_ID)
            == 1
        )


@pytest.mark.asyncio
async def test_eight_concurrent_expires_elect_exactly_one_close(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def contend(index: int):
        await start.wait()
        try:
            async with PostgresAssignmentGateway(
                pool, offer_token_hash=OFFER_TOKEN_HASH
            ) as gateway:
                snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
                closed = snapshot.run.expire_precommit_assignment(
                    assignment_id="assignment-001",
                    expiry_key=f"expiry-{index:03d}",
                    observed_at=EXPIRES_AT,
                    expected_version=snapshot.version,
                )
                return await gateway.publish_close(closed=closed, expected=snapshot)
        except BaseException as error:
            return error

    results = await asyncio.gather(*(contend(i) for i in range(contenders)))
    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [
        result for result in results if isinstance(result, (VersionConflict, AssignmentConflict))
    ]
    assert len(winners) == 1, results
    assert len(conflicts) == contenders - 1
    assert winners[0].value.state is RunState.QUEUED
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT state FROM qep_assignments") == "expired_prestart"
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "queued"
        )


@pytest.mark.asyncio
async def test_publish_close_rejects_stale_snapshot(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    from qarunner.application.ports.assignment import AssignmentMutationSnapshot

    stale = AssignmentMutationSnapshot(run=Run.create(run_id=RUN_ID))
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            await gateway.publish_close(closed=closed, expected=stale)


@pytest.mark.asyncio
async def test_publish_close_rejects_run_id_mismatch(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id="other-run",
                version=closed.version,
                state=closed.state,
                current_assignment_id=None,
                assignment=None,
                assignments=closed.assignments,
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "run_id_mismatch"


@pytest.mark.asyncio
async def test_publish_close_rejects_when_assignment_row_already_closed(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_assignments SET state = 'expired_prestart' WHERE id = $1",
                "assignment-001",
            )
            await gateway.publish_close(closed=closed, expected=snapshot)


@pytest.mark.asyncio
async def test_publish_close_rejects_non_queued_domain_result(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id=RUN_ID,
                version=closed.version,
                state=RunState.ASSIGNED,
                current_assignment_id=None,
                assignment=None,
                assignments=closed.assignments,
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "not_queued"


@pytest.mark.asyncio
async def test_publish_close_rejects_when_current_assignment_not_cleared(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id=RUN_ID,
                version=closed.version,
                state=RunState.QUEUED,
                current_assignment_id="assignment-001",
                assignment=closed.assignments[-1],
                assignments=closed.assignments,
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "current_assignment_not_cleared"


@pytest.mark.asyncio
async def test_publish_close_rejects_missing_closed_assignment(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            forged = SimpleNamespace(
                id=RUN_ID,
                version=snapshot.version + 1,
                state=RunState.QUEUED,
                current_assignment_id=None,
                assignment=None,
                assignments=(),
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "missing_closed_assignment"


@pytest.mark.asyncio
async def test_publish_close_rejects_missing_closure(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    from qarunner.domain.assignment import AssignmentState

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            fake_assignment = SimpleNamespace(
                id="assignment-001",
                state=AssignmentState.EXPIRED_PRESTART,
                closure=None,
            )
            forged = SimpleNamespace(
                id=RUN_ID,
                version=snapshot.version + 1,
                state=RunState.QUEUED,
                current_assignment_id=None,
                assignment=None,
                assignments=(fake_assignment,),
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "missing_closure"


@pytest.mark.asyncio
async def test_publish_close_rejects_not_prestart_closed_state(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    from qarunner.domain.assignment import AssignmentState

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            fake_assignment = SimpleNamespace(
                id="assignment-001",
                state=AssignmentState.OFFERED,
                closure=object(),
            )
            forged = SimpleNamespace(
                id=RUN_ID,
                version=snapshot.version + 1,
                state=RunState.QUEUED,
                current_assignment_id=None,
                assignment=None,
                assignments=(fake_assignment,),
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "not_prestart_closed"


@pytest.mark.asyncio
async def test_publish_close_rejects_closed_assignment_mismatch(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    from qarunner.domain.assignment import AssignmentState

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            fake_assignment = SimpleNamespace(
                id="assignment-other",
                state=AssignmentState.EXPIRED_PRESTART,
                closure=object(),
            )
            forged = SimpleNamespace(
                id=RUN_ID,
                version=snapshot.version + 1,
                state=RunState.QUEUED,
                current_assignment_id=None,
                assignment=None,
                assignments=(fake_assignment,),
            )
            await gateway.publish_close(closed=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "closed_assignment_mismatch"


@pytest.mark.asyncio
async def test_publish_close_rejects_when_expected_not_precommit(
    assignment_store: PostgresStore,
) -> None:
    """Force expected active assignment state to COMMITTED (illegal for close)."""
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    from qarunner.domain.assignment import AssignmentState

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION),
            release_key="release-001",
            observed_at=OFFERED_AT + timedelta(minutes=2),
            expected_version=snapshot.version,
        )

        class _Expected:
            def __init__(self, run) -> None:
                self.run = run
                self.run_id = run.id
                self.version = run.version

            def __eq__(self, other) -> bool:
                return other is gateway._snapshot

        fake_active = SimpleNamespace(id="assignment-001", state=AssignmentState.COMMITTED)
        expected_forged = _Expected(
            SimpleNamespace(
                id=RUN_ID,
                version=snapshot.version,
                state=RunState.ASSIGNED,
                assignment=fake_active,
                assignments=snapshot.run.assignments,
                current_assignment_id="assignment-001",
            )
        )
        with pytest.raises(PortContractError) as exc:
            await gateway._publish_close(closed=closed, expected=expected_forged)  # type: ignore[arg-type]
        assert exc.value.reason == "expected_not_precommit"
        gateway._aborted = True


async def _commit_start_once(
    pool: asyncpg.Pool,
    *,
    assignment_id: str = "assignment-001",
    start_commit_key: str = "start-commit-001",
    new_attempt_id: str = "attempt-001",
    observed_at: datetime | None = None,
    worker_ref: WorkerRef | None = None,
    spec_digest: Digest | None = None,
) -> CommitStartResult:
    observed = observed_at if observed_at is not None else OFFERED_AT + timedelta(minutes=2)
    worker = (
        worker_ref
        if worker_ref is not None
        else WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    )
    digest = spec_digest if spec_digest is not None else SPEC_DIGEST
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        commit = snapshot.run.commit_start(
            assignment_id=assignment_id,
            worker=worker,
            start_commit_key=start_commit_key,
            spec_digest=digest,
            new_attempt_id=new_attempt_id,
            observed_at=observed,
            expected_version=snapshot.version,
        )
        result = await gateway.publish_commit_start(commit=commit, expected=snapshot)
    return result.value


@pytest.mark.asyncio
async def test_single_commit_start_creates_attempt_and_runs(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")

    commit = await _commit_start_once(pool)

    assert commit.replayed is False
    assert commit.fence == 1
    assert commit.run.state is RunState.RUNNING
    assert commit.run.version == 3
    assert commit.run.current_fence == 1
    assert commit.attempt.id == "attempt-001"
    assert commit.attempt.start_commit_key == "start-commit-001"
    assert commit.attempt.state.value == "start_committed"
    async with pool.acquire() as connection:
        assignment = await connection.fetchrow(
            "SELECT state, attempt_id, fence, version FROM qep_assignments"
        )
        attempt = await connection.fetchrow(
            "SELECT id, fence, state, start_commit_key, attempt_no FROM qep_attempts"
        )
        run = await connection.fetchrow(
            "SELECT orchestration_phase, version, current_fence, attempt_count "
            "FROM qep_runs WHERE id = $1",
            RUN_ID,
        )
    assert dict(assignment) == {
        "state": "committed",
        "attempt_id": "attempt-001",
        "fence": 1,
        "version": 2,
    }
    assert dict(attempt) == {
        "id": "attempt-001",
        "fence": 1,
        "state": "start_committed",
        "start_commit_key": "start-commit-001",
        "attempt_no": 1,
    }
    assert dict(run) == {
        "orchestration_phase": "running",
        "version": 3,
        "current_fence": 1,
        "attempt_count": 1,
    }


@pytest.mark.asyncio
async def test_eight_concurrent_commit_starts_elect_exactly_one_fence(
    assignment_store: PostgresStore,
) -> None:
    """ASGN-COMMIT-START core: 8 contenders → one attempt/fence, rest VersionConflict."""
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    contenders = 8
    start = asyncio.Barrier(contenders)
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    observed = OFFERED_AT + timedelta(minutes=2)

    async def contend(index: int):
        await start.wait()
        try:
            async with PostgresAssignmentGateway(
                pool, offer_token_hash=OFFER_TOKEN_HASH
            ) as gateway:
                snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
                commit = snapshot.run.commit_start(
                    assignment_id="assignment-001",
                    worker=worker,
                    start_commit_key=f"start-commit-{index:03d}",
                    spec_digest=SPEC_DIGEST,
                    new_attempt_id=f"attempt-{index:03d}",
                    observed_at=observed,
                    expected_version=snapshot.version,
                )
                return await gateway.publish_commit_start(commit=commit, expected=snapshot)
        except BaseException as error:
            return error

    results = await asyncio.gather(*(contend(i) for i in range(contenders)))
    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [
        result for result in results if isinstance(result, (VersionConflict, AssignmentConflict))
    ]
    assert len(winners) == 1, results
    assert len(conflicts) == contenders - 1
    assert winners[0].value.fence == 1
    assert winners[0].value.run.state is RunState.RUNNING

    async with pool.acquire() as connection:
        attempt_count = await connection.fetchval("SELECT count(*) FROM qep_attempts")
        fence = await connection.fetchval(
            "SELECT current_fence FROM qep_runs WHERE id = $1", RUN_ID
        )
        phase = await connection.fetchval(
            "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
        )
    assert attempt_count == 1
    assert fence == 1
    assert phase == "running"


@pytest.mark.asyncio
async def test_commit_start_exact_replay_returns_same_attempt(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    first = await _commit_start_once(pool)
    # Domain exact-replay path: rehydrate RUNNING and re-issue same start_commit_key.
    second = await _commit_start_once(pool)
    assert first.attempt.id == second.attempt.id
    assert first.fence == second.fence
    assert second.replayed is True
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_attempts") == 1
        assert await connection.fetchval("SELECT version FROM qep_runs WHERE id = $1", RUN_ID) == 3


@pytest.mark.asyncio
async def test_commit_start_rejects_different_digest_same_key(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    await _commit_start_once(pool)
    other_digest = canonical_digest(
        schema_version="qep.test-assignment-offer.v1",
        payload={"label": "different-spec"},
    )
    with pytest.raises(IdempotencyConflict):
        await _commit_start_once(pool, spec_digest=other_digest)


@pytest.mark.asyncio
async def test_commit_start_rejects_stale_run_version(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_runs SET version = version + 10 WHERE id = $1",
                RUN_ID,
            )
            await gateway.publish_commit_start(commit=commit, expected=snapshot)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_attempts") == 0
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "assigned"
        )


@pytest.mark.asyncio
async def test_commit_start_rejects_stale_snapshot(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    from qarunner.application.ports.assignment import AssignmentMutationSnapshot

    stale = AssignmentMutationSnapshot(run=Run.create(run_id=RUN_ID))
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            await gateway.publish_commit_start(commit=commit, expected=stale)


@pytest.mark.asyncio
async def test_commit_start_rejects_non_commit_result(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_commit_start(
                commit=object(),  # type: ignore[arg-type]
                expected=snapshot,
            )
    assert exc.value.reason == "not_commit_result"


@pytest.mark.asyncio
async def test_commit_start_rejects_run_id_mismatch(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            forged_run = SimpleNamespace(
                id="other-run",
                state=RunState.RUNNING,
                assignment=commit.run.assignment,
                current_fence=commit.fence,
            )
            forged = CommitStartResult(
                run=forged_run,  # type: ignore[arg-type]
                attempt=commit.attempt,
                fence=commit.fence,
                replayed=False,
            )
            await gateway.publish_commit_start(commit=forged, expected=snapshot)
    assert exc.value.reason == "run_id_mismatch"


@pytest.mark.asyncio
async def test_commit_start_rejects_non_running_domain_result(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            forged_run = SimpleNamespace(
                id=RUN_ID,
                state=RunState.ASSIGNED,
                assignment=commit.run.assignment,
                current_fence=commit.fence,
            )
            forged = CommitStartResult(
                run=forged_run,  # type: ignore[arg-type]
                attempt=commit.attempt,
                fence=commit.fence,
                replayed=False,
            )
            await gateway.publish_commit_start(commit=forged, expected=snapshot)
    assert exc.value.reason == "not_running"


@pytest.mark.asyncio
async def test_commit_start_rejects_when_assignment_not_committed_in_domain(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            # Drop the assignment pointer so publish sees not_committed.
            forged_run = SimpleNamespace(
                id=RUN_ID,
                state=RunState.RUNNING,
                assignment=None,
                current_fence=1,
            )
            forged = CommitStartResult(
                run=forged_run,  # type: ignore[arg-type]
                attempt=commit.attempt,
                fence=1,
                replayed=False,
            )
            await gateway.publish_commit_start(commit=forged, expected=snapshot)
    assert exc.value.reason == "not_committed"


@pytest.mark.asyncio
async def test_commit_start_rejects_missing_committed_at(
    assignment_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    from qarunner.domain.assignment import AssignmentState

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            fake_assignment = SimpleNamespace(
                id="assignment-001",
                state=AssignmentState.COMMITTED,
                committed_at=None,
            )
            forged_run = SimpleNamespace(
                id=RUN_ID,
                state=RunState.RUNNING,
                assignment=fake_assignment,
                current_fence=1,
            )
            forged = CommitStartResult(
                run=forged_run,  # type: ignore[arg-type]
                attempt=commit.attempt,
                fence=1,
                replayed=False,
            )
            await gateway.publish_commit_start(commit=forged, expected=snapshot)
    assert exc.value.reason == "missing_committed_at"


@pytest.mark.asyncio
async def test_commit_start_rejects_fence_mismatch(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            forged = CommitStartResult(
                run=commit.run,
                attempt=commit.attempt,
                fence=99,
                replayed=False,
            )
            await gateway.publish_commit_start(commit=forged, expected=snapshot)
    assert exc.value.reason == "fence_mismatch"


@pytest.mark.asyncio
async def test_commit_start_suppressed_attempt_insert_sticky_aborts(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_attempt_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_attempt_insert
            BEFORE INSERT ON qep_attempts
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_attempt_insert()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            await gateway.publish_commit_start(commit=commit, expected=snapshot)
    assert exc.value.reason == "insert_suppressed"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_attempts") == 0
        assert (
            await connection.fetchval(
                "SELECT orchestration_phase FROM qep_runs WHERE id = $1", RUN_ID
            )
            == "assigned"
        )


@pytest.mark.asyncio
async def test_commit_start_rejects_when_assignment_row_version_bumped(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            commit = snapshot.run.commit_start(
                assignment_id="assignment-001",
                worker=worker,
                start_commit_key="start-commit-001",
                spec_digest=SPEC_DIGEST,
                new_attempt_id="attempt-001",
                observed_at=OFFERED_AT + timedelta(minutes=2),
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_assignments SET version = version + 10 WHERE id = $1",
                "assignment-001",
            )
            await gateway.publish_commit_start(commit=commit, expected=snapshot)


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_running_without_committed_assignment(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_runs SET orchestration_phase = 'running', current_fence = 1, "
            "version = 3 WHERE id = $1",
            RUN_ID,
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id=RUN_ID)
    assert exc.value.reason == "active_assignment_missing"


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_running_without_current_attempt(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    async with pool.acquire() as connection:
        # Force committed assignment + running phase without an attempt row.
        await connection.execute(
            """
            UPDATE qep_assignments
            SET state = 'committed', committed_at = $1, attempt_id = 'ghost', fence = 1,
                version = 2
            WHERE id = 'assignment-001'
            """,
            OFFERED_AT + timedelta(minutes=2),
        )
        await connection.execute(
            "UPDATE qep_runs SET orchestration_phase = 'running', current_fence = 1, "
            "attempt_count = 1, version = 3 WHERE id = $1",
            RUN_ID,
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id=RUN_ID)
    assert exc.value.reason == "current_attempt_missing"


@pytest.mark.asyncio
async def test_gateway_lifecycle_rejects_reentry_and_use_after_close(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    gateway = PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH)
    async with gateway:
        with pytest.raises(PortContractError) as reenter:
            await gateway.__aenter__()
        assert reenter.value.reason == "already_active"
    with pytest.raises(PortContractError) as closed:
        async with gateway:
            pass
    assert closed.value.reason == "closed"


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_locking_a_second_run(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        await gateway.get_run_for_update(run_id=RUN_ID)
        with pytest.raises(PortContractError) as exc:
            await gateway.get_run_for_update(run_id="another-run")
    assert exc.value.reason == "aggregate_already_locked"


@pytest.mark.asyncio
async def test_methods_reject_use_outside_active_transaction(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    gateway = PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH)
    with pytest.raises(PortContractError) as exc:
        await gateway.get_run_for_update(run_id=RUN_ID)
    assert exc.value.reason == "not_active"


@pytest.mark.asyncio
async def test_publish_offer_rejects_assigned_run_missing_assignment(
    assignment_store: PostgresStore,
) -> None:
    """Branch coverage: ASSIGNED domain result without a current assignment pointer."""
    from types import SimpleNamespace

    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    bogus = SimpleNamespace(
        id=RUN_ID,
        state=RunState.ASSIGNED,
        assignment=None,
    )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            await gateway.publish_offer(offered=bogus, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "missing_assignment"


@pytest.mark.asyncio
async def test_get_run_for_update_rejects_assigned_phase_with_only_closed_rows(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=EXPIRES_AT,
            expected_version=snapshot.version,
        )
        await gateway.publish_close(closed=closed, expected=snapshot)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_runs SET orchestration_phase = 'assigned' WHERE id = $1",
            RUN_ID,
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            await gateway.get_run_for_update(run_id=RUN_ID)
    assert exc.value.reason == "active_assignment_missing"


@pytest.mark.asyncio
async def test_publish_close_rejects_stale_run_version(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    with pytest.raises(VersionConflict):
        async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
            snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
            closed = snapshot.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="expiry-001",
                observed_at=EXPIRES_AT,
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_runs SET version = version + 10 WHERE id = $1",
                RUN_ID,
            )
            await gateway.publish_close(closed=closed, expected=snapshot)


@pytest.mark.asyncio
async def test_closed_assignment_payload_round_trips_worker(
    assignment_store: PostgresStore,
) -> None:
    pool = assignment_store._require_pool()
    await _seed_queued_run(pool)
    await _offer_once(pool, assignment_id="assignment-001")
    await _claim_once(pool, assignment_id="assignment-001")
    worker = WorkerRef(worker_id=WORKER_ID, generation=WORKER_GENERATION)
    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
        closed = snapshot.run.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker,
            release_key="release-001",
            observed_at=OFFERED_AT + timedelta(minutes=2),
            expected_version=snapshot.version,
        )
        await gateway.publish_close(closed=closed, expected=snapshot)

    async with PostgresAssignmentGateway(pool, offer_token_hash=OFFER_TOKEN_HASH) as gateway:
        snapshot = await gateway.get_run_for_update(run_id=RUN_ID)
    historical = snapshot.run.assignments[-1]
    assert historical.state.value == "released_prestart"
    assert historical.closure is not None
    assert historical.closure.worker == worker
    assert historical.closure.cancellation_intent_digest is None


def test_closure_from_payload_rejects_non_dict() -> None:
    from qarunner.adapters.postgres_assignment import _closure_from_payload

    assert _closure_from_payload(None) is None
    assert _closure_from_payload("not-a-dict") is None
    assert _closure_from_payload(123) is None
