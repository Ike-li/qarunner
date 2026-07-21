"""Real PostgreSQL transaction coverage for greenfield Run finalization."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator
from tests.unit.application.test_finalize_run import bound_basis, command
from tests.unit.domain.test_run_finalization_basis import d

from qarunner.adapters.postgres_run_finalization import PostgresRunFinalizationUnitOfWork
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.run_finalization import (
    FinalizeRunAuthority,
    RunFinalizationGateway,
    RunFinalizationMutationSnapshot,
    RunFinalizationProjection,
    RunFinalizationPublication,
    RunFinalizationSideEffect,
)
from qarunner.application.run_closed_handoff import build_run_closed_handoff
from qarunner.application.run_finalization import FinalizeRun
from qarunner.domain import (
    AttemptExecutionFact,
    Digest,
    EffectiveItemResolution,
    EffectiveSourceKind,
    IdempotencyConflict,
    ItemAggregationClass,
    OriginalItemResolution,
    OriginalSourceKind,
    RunDisposition,
    RunFinalizationState,
    RunItemKey,
    RunItemResolution,
    RunItemResolutionSet,
    RunOutcome,
    RunPhase,
    TerminalInputKind,
    VersionConflict,
)


@pytest.fixture
async def run_finalization_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-run-finalization-admin-password-at-least-32-chars",
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


@pytest.mark.asyncio
async def test_run_finalization_commits_all_facts_once_and_exactly_replays(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        assert isinstance(gateway, RunFinalizationGateway)
        first = await FinalizeRun(gateway=gateway).execute(expected)

    assert not first.replayed
    async with pool.acquire() as connection:
        persisted = await _publication_snapshot(connection)

    assert persisted == {
        "run_phase": "closed",
        "run_disposition": "closed_no_retry",
        "run_outcome": "passed",
        "run_version": 1,
        "attempt_state": "passed",
        "attempt_version": 1,
        "basis_count": 1,
        "resolution_count": 1,
        "audit_count": 1,
        "outbox_count": 1,
        "outbox_event_type": "run.closed.v1",
    }
    async with pool.acquire() as connection:
        basis_row = await connection.fetchrow(
            "SELECT basis_digest, payload FROM qep_run_finalization_bases"
        )
        resolution_row = await connection.fetchrow(
            "SELECT item_resolution_digest, payload FROM qep_run_item_resolutions"
        )
        audit_row = await connection.fetchrow(
            "SELECT before_digest, after_digest, payload FROM qep_audit_events"
        )
        outbox_row = await connection.fetchrow(
            """
            SELECT event_id, aggregate_type, aggregate_id, payload_digest, payload
            FROM qep_outbox_events
            """
        )
    assert basis_row is not None
    assert basis_row["basis_digest"] == _digest_hex(expected.candidate_basis.basis_digest)
    assert json.loads(basis_row["payload"]) == expected.candidate_basis.canonical_payload()
    assert resolution_row is not None
    entry = expected.resolution_set.entries[0]
    assert resolution_row["item_resolution_digest"] == _digest_hex(entry.item_resolution_digest)
    assert json.loads(resolution_row["payload"]) == entry.canonical_payload()
    assert audit_row is not None
    audit_payload = json.loads(audit_row["payload"])
    assert audit_row["before_digest"] is None
    assert audit_row["after_digest"] == _digest_hex(expected.candidate_basis.basis_digest)
    assert audit_payload["basis_digest"] == expected.candidate_basis.basis_digest.value
    assert outbox_row is not None
    outbox_payload = json.loads(outbox_row["payload"])
    expected_handoff = build_run_closed_handoff(
        basis=expected.candidate_basis,
        resolution_set=expected.resolution_set,
        authority_digest=Digest(audit_payload["authority_digest"]),
        write_epoch=outbox_payload["write_epoch"],
    )
    assert (
        outbox_row["event_id"],
        outbox_row["aggregate_type"],
        outbox_row["aggregate_id"],
        outbox_row["payload_digest"],
    ) == (
        expected_handoff.event_id,
        "run",
        expected_handoff.run_id,
        _digest_hex(expected_handoff.payload_digest),
    )
    assert outbox_payload["run_basis_digest"] == expected_handoff.run_basis_digest.value

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        replay = await FinalizeRun(gateway=gateway).execute(
            command(expected_run_version=99, expected_attempt_version=99)
        )

    assert replay.replayed
    assert replay.projection == first.projection
    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == persisted


@pytest.mark.asyncio
async def test_concurrent_identical_run_finalizations_commit_once_and_exactly_replay(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def finalize():
        await start.wait()
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            return await FinalizeRun(gateway=gateway).execute(expected)

    results = await asyncio.gather(*(finalize() for _ in range(contenders)))

    assert sum(result.replayed for result in results) == contenders - 1
    assert {result.projection for result in results} == {results[0].projection}
    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == {
            "run_phase": "closed",
            "run_disposition": "closed_no_retry",
            "run_outcome": "passed",
            "run_version": 1,
            "attempt_state": "passed",
            "attempt_version": 1,
            "basis_count": 1,
            "resolution_count": 1,
            "audit_count": 1,
            "outbox_count": 1,
            "outbox_event_type": "run.closed.v1",
        }


@pytest.mark.asyncio
async def test_concurrent_conflicting_run_finalizations_elect_one_terminal_basis(
    run_finalization_store: PostgresStore,
) -> None:
    first = command()
    changed = command(candidate_basis=replace(bound_basis(), terminal_rule_digest=d("e")))
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, first)
    start = asyncio.Barrier(2)

    async def finalize(finalize_command):
        await start.wait()
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            return await FinalizeRun(gateway=gateway).execute(finalize_command)

    results = await asyncio.gather(
        finalize(first),
        finalize(changed),
        return_exceptions=True,
    )

    conflicts = [result for result in results if isinstance(result, IdempotencyConflict)]
    winners = [result for result in results if not isinstance(result, BaseException)]
    assert len(conflicts) == 1
    assert len(winners) == 1
    winner = winners[0]
    async with pool.acquire() as connection:
        persisted = await _publication_snapshot(connection)
        stored_basis_digest = await connection.fetchval(
            "SELECT basis_digest FROM qep_run_finalization_bases"
        )
    assert stored_basis_digest == _digest_hex(winner.projection.basis_digest)
    assert persisted["basis_count"] == 1
    assert persisted["resolution_count"] == 1
    assert persisted["audit_count"] == 1
    assert persisted["outbox_count"] == 1


@pytest.mark.asyncio
async def test_outbox_conflict_rolls_back_every_run_finalization_participant(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    handoff = build_run_closed_handoff(
        basis=expected.candidate_basis,
        resolution_set=expected.resolution_set,
        authority_digest=Digest("sha256:" + "f" * 64),
        write_epoch=1,
    )
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, available_at, created_at
            ) VALUES ($1, $2, 'run', $3, 'poison.v1', $4, $5, $6, $6)
            """,
            "preexisting-poison-event",
            handoff.event_id,
            handoff.run_id,
            "e" * 64,
            json.dumps({"poison": True}),
            datetime(2026, 7, 18, 12, tzinfo=UTC),
        )

    with pytest.raises(asyncpg.UniqueViolationError):
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            await FinalizeRun(gateway=gateway).execute(expected)

    async with pool.acquire() as connection:
        assert await _fact_snapshot(connection) == {
            "run_phase": "running",
            "run_version": 0,
            "attempt_state": "uploading",
            "attempt_version": 0,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 1,
        }


@pytest.mark.asyncio
async def test_caught_publish_failure_still_aborts_the_run_unit_of_work(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_run_update() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_run_update
            BEFORE UPDATE ON qep_runs
            FOR EACH ROW EXECUTE FUNCTION suppress_run_update()
            """
        )

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        with pytest.raises(VersionConflict):
            await FinalizeRun(gateway=gateway).execute(expected)

    async with pool.acquire() as connection:
        assert await _fact_snapshot(connection) == {
            "run_phase": "running",
            "run_version": 0,
            "attempt_state": "uploading",
            "attempt_version": 0,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
async def test_invalid_evidence_aborts_the_unit_of_work_and_all_future_operations(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_evidence_index SET root_digest = $1 WHERE attempt_id = $2",
            "e" * 64,
            "attempt-1",
        )

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        with pytest.raises(AuthorityStateConflict) as invalid:
            await FinalizeRun(gateway=gateway).execute(expected)
        assert invalid.value.reason == "run_finalization_evidence_binding_invalid"

        with pytest.raises(PortContractError) as aborted:
            await FinalizeRun(gateway=gateway).execute(expected)
        assert aborted.value.resource == "unit_of_work"
        assert aborted.value.field == "state"
        assert aborted.value.reason == "aborted"

    async with pool.acquire() as connection:
        assert await _fact_snapshot(connection) == {
            "run_phase": "running",
            "run_version": 0,
            "attempt_state": "uploading",
            "attempt_version": 0,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
async def test_forged_attempt_basis_without_evidence_digest_fails_closed(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    forged = copy.copy(expected.candidate_basis)
    object.__setattr__(forged, "evidence_root_digest", None)
    forged_command = command(candidate_basis=forged)

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            await FinalizeRun(gateway=gateway).execute(forged_command)

    assert invalid.value.reason == "run_finalization_evidence_binding_invalid"
    async with pool.acquire() as connection:
        assert (await _fact_snapshot(connection))["basis_count"] == 0


@pytest.mark.asyncio
async def test_live_authority_is_revalidated_before_terminal_replay(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        await FinalizeRun(gateway=gateway).execute(expected)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batches SET write_epoch = 0 WHERE id = $1",
            expected.candidate_basis.batch_id,
        )

    with pytest.raises(AuthorityStateConflict) as denied:
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            await FinalizeRun(gateway=gateway).execute(expected)

    assert denied.value.reason == "run_finalization_authority_missing"
    async with pool.acquire() as connection:
        assert (await _publication_snapshot(connection))["outbox_count"] == 1


@pytest.mark.asyncio
async def test_missing_and_corrupt_terminal_authority_fail_closed(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)

    with pytest.raises(AuthorityPermissionDenied):
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            await gateway.require_finalization_authority(run_id="missing-run")

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = 'closed',
                disposition = 'closed_no_retry',
                outcome = 'passed',
                finalization_basis_digest = $1
            WHERE id = $2
            """,
            "e" * 64,
            expected.candidate_basis.run_id,
        )
    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
            await gateway.require_finalization_authority(run_id=expected.candidate_basis.run_id)
    assert corrupt.value.reason == "run_finalization_terminal_binding_invalid"


@pytest.mark.asyncio
async def test_run_unit_of_work_is_one_shot_and_one_aggregate_scoped(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    inactive = PostgresRunFinalizationUnitOfWork(pool)
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.require_finalization_authority(run_id="run-1")
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.__aexit__(None, None, None)

    unit_of_work = PostgresRunFinalizationUnitOfWork(pool)
    async with unit_of_work as gateway:
        with pytest.raises(PortContractError, match="already_active"):
            await gateway.__aenter__()
        with pytest.raises(PortContractError, match="authority_not_locked"):
            await gateway.lookup_stored(
                identity_scope=("qep.run-finalization-basis.v1", "run-1", 0)
            )
        authority = await gateway.require_finalization_authority(run_id="run-1")
        assert await gateway.require_finalization_authority(run_id="run-1") == authority
        with pytest.raises(PortContractError, match="aggregate_already_locked"):
            await gateway.require_finalization_authority(run_id="other-run")
        with pytest.raises(PortContractError, match="unsupported"):
            await gateway.lookup_stored(identity_scope=("unsupported", "run-1", 0))

    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.require_finalization_authority(run_id="run-1")
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


@pytest.mark.asyncio
async def test_prestart_finalization_persists_without_fabricating_an_attempt(
    run_finalization_store: PostgresStore,
) -> None:
    expected = _prestart_command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        first = await FinalizeRun(gateway=gateway).execute(expected)

    assert not first.replayed
    async with pool.acquire() as connection:
        persisted = await _publication_snapshot(connection)
    assert persisted == {
        "run_phase": "closed",
        "run_disposition": "closed_no_retry",
        "run_outcome": "cancelled",
        "run_version": 1,
        "attempt_state": None,
        "attempt_version": None,
        "basis_count": 1,
        "resolution_count": 1,
        "audit_count": 1,
        "outbox_count": 1,
        "outbox_event_type": "run.closed.v1",
    }

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        replay = await FinalizeRun(gateway=gateway).execute(
            _prestart_command(expected_run_version=99)
        )
    assert replay.replayed
    assert replay.projection == first.projection


@pytest.mark.asyncio
async def test_direct_publication_requires_the_locked_authority_and_snapshot(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        authority = await gateway.require_finalization_authority(run_id="run-1")
        snapshot = RunFinalizationMutationSnapshot("run-1", 0, "attempt-1", 0)
        superseded = replace(authority, write_epoch=authority.write_epoch + 1)
        with pytest.raises(AuthorityStateConflict) as mismatch:
            await gateway.publish_finalization(
                publication=_publication(expected, superseded, snapshot)
            )
        assert mismatch.value.reason == "publication_authority_superseded"

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        authority = await gateway.require_finalization_authority(run_id="run-1")
        with pytest.raises(VersionConflict):
            await gateway.publish_finalization(
                publication=_publication(expected, authority, snapshot)
            )

    async with pool.acquire() as connection:
        assert (await _fact_snapshot(connection))["basis_count"] == 0


@pytest.mark.asyncio
async def test_direct_publication_exactly_replays_and_rejects_a_changed_digest(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        first = await FinalizeRun(gateway=gateway).execute(expected)

    snapshot = RunFinalizationMutationSnapshot("run-1", 0, "attempt-1", 0)
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        authority = await gateway.require_finalization_authority(run_id="run-1")
        replay = await gateway.publish_finalization(
            publication=_publication(expected, authority, snapshot)
        )
    assert replay.replayed
    assert replay.value == first.projection

    changed = command(candidate_basis=replace(bound_basis(), terminal_rule_digest=d("e")))
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        authority = await gateway.require_finalization_authority(run_id="run-1")
        with pytest.raises(IdempotencyConflict):
            await gateway.publish_finalization(
                publication=_publication(changed, authority, snapshot)
            )
    async with pool.acquire() as connection:
        assert (await _publication_snapshot(connection))["outbox_count"] == 1


@pytest.mark.asyncio
async def test_attempt_cas_failure_rolls_back_all_staged_facts_when_caught(
    run_finalization_store: PostgresStore,
) -> None:
    expected = command()
    pool = run_finalization_store._require_pool()
    await _seed_finalizable_run(pool, expected)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_attempt_update() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_attempt_update
            BEFORE UPDATE ON qep_attempts
            FOR EACH ROW EXECUTE FUNCTION suppress_attempt_update()
            """
        )

    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        with pytest.raises(VersionConflict) as conflict:
            await FinalizeRun(gateway=gateway).execute(expected)
        assert conflict.value.entity_type == "attempt"

    async with pool.acquire() as connection:
        assert await _fact_snapshot(connection) == {
            "run_phase": "running",
            "run_version": 0,
            "attempt_state": "uploading",
            "attempt_version": 0,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


async def _seed_finalizable_run(pool: asyncpg.Pool, finalize_command) -> None:
    basis = finalize_command.candidate_basis
    resolved = finalize_command.resolution_set
    has_attempt = basis.final_attempt_id is not None
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    later = now + timedelta(minutes=5)
    empty = json.dumps({})
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
            _digest_hex(basis.execution_spec_digest),
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
            basis.batch_id,
            "project-1",
            "suite-revision-1",
            "b" * 64,
            "batch:create",
            "batch-1",
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
            basis.batch_id,
            "qep.case-manifest.v1",
            _digest_hex(basis.manifest_digest),
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
            basis.batch_id,
            _digest_hex(basis.shard_plan_digest),
            empty,
            now,
        )
        await connection.execute(
            """
            INSERT INTO qep_runs (
                id, batch_id, plan_id, shard_index, resource_profile_id,
                orchestration_phase, current_fence, attempt_count, version,
                run_item_set_digest, created_at, updated_at, payload
            ) VALUES ($1, $2, $3, 0, $4, 'running', $5, $6, 0, $7, $8, $8, $9)
            """,
            basis.run_id,
            basis.batch_id,
            "plan-1",
            "profile-1",
            1 if has_attempt else 0,
            1 if has_attempt else 0,
            _digest_hex(basis.run_item_set_digest),
            now,
            empty,
        )
        await connection.execute(
            """
            INSERT INTO qep_run_manifest_items (run_id, manifest_id, item_index)
            VALUES ($1, $2, 0)
            """,
            basis.run_id,
            "manifest-1",
        )
        if has_attempt:
            await connection.execute(
                """
                INSERT INTO qep_workers (
                    id, host_id, current_generation, status, pool_id,
                    last_seen_at, version, created_at
                ) VALUES ($1, $2, 1, 'busy', $3, $4, 0, $4)
                """,
                "worker-1",
                "host-1",
                "pool-1",
                now,
            )
            await connection.execute(
                """
                INSERT INTO qep_worker_generations (
                    worker_id, generation, cert_serial, agent_version,
                    capabilities_digest, registered_at
                ) VALUES ($1, 1, $2, $3, $4, $5)
                """,
                "worker-1",
                "cert-1",
                "1.0.0",
                "c" * 64,
                now,
            )
            await connection.execute(
                """
                INSERT INTO qep_assignments (
                    id, run_id, worker_id, worker_generation, spec_digest, offer_token_hash,
                    state, offered_at, expires_at, claimed_at, committed_at,
                    attempt_id, fence, version, payload
                ) VALUES ($1, $2, $3, 1, $4, $5, 'committed', $6, $7, $6, $6,
                          $8, 1, 0, $9)
                """,
                "assignment-1",
                basis.run_id,
                "worker-1",
                _digest_hex(basis.execution_spec_digest),
                "d" * 64,
                now,
                later,
                basis.final_attempt_id,
                empty,
            )
            await connection.execute(
                """
                INSERT INTO qep_attempts (
                    id, run_id, attempt_no, fence, assignment_id, worker_id,
                    worker_generation, spec_digest, start_commit_key, state,
                    version, started_at, payload
                ) VALUES ($1, $2, 1, 1, $3, $4, 1, $5, $6, 'uploading', 0, $7, $8)
                """,
                basis.final_attempt_id,
                basis.run_id,
                "assignment-1",
                "worker-1",
                _digest_hex(basis.execution_spec_digest),
                "start-attempt-1",
                now,
                empty,
            )
            await connection.execute(
                """
                INSERT INTO qep_evidence_index (
                    id, attempt_id, assignment_id, fence, schema_version,
                    root_digest, payload, finalized_at
                ) VALUES ($1, $2, $3, 1, $4, $5, $6, $7)
                """,
                "evidence-1",
                basis.final_attempt_id,
                "assignment-1",
                "qep.evidence-manifest.v1",
                _digest_hex(basis.evidence_root_digest),
                empty,
                now,
            )
    assert resolved.entries[0].item_key.manifest_id == "manifest-1"


async def _publication_snapshot(connection: asyncpg.Connection) -> dict[str, object]:
    row = await connection.fetchrow(
        """
        SELECT
            run.orchestration_phase AS run_phase,
            run.disposition AS run_disposition,
            run.outcome AS run_outcome,
            run.version AS run_version,
            attempt.state AS attempt_state,
            attempt.version AS attempt_version,
            (SELECT count(*) FROM qep_run_finalization_bases) AS basis_count,
            (SELECT count(*) FROM qep_run_item_resolutions) AS resolution_count,
            (SELECT count(*) FROM qep_audit_events) AS audit_count,
            (SELECT count(*) FROM qep_outbox_events) AS outbox_count,
            (SELECT event_type FROM qep_outbox_events) AS outbox_event_type
        FROM qep_runs AS run
        LEFT JOIN qep_attempts AS attempt ON attempt.run_id = run.id
        WHERE run.id = 'run-1'
        """
    )
    assert row is not None
    return dict(row)


async def _fact_snapshot(connection: asyncpg.Connection) -> dict[str, object]:
    row = await connection.fetchrow(
        """
        SELECT
            run.orchestration_phase AS run_phase,
            run.version AS run_version,
            attempt.state AS attempt_state,
            attempt.version AS attempt_version,
            (SELECT count(*) FROM qep_run_finalization_bases) AS basis_count,
            (SELECT count(*) FROM qep_run_item_resolutions) AS resolution_count,
            (SELECT count(*) FROM qep_audit_events) AS audit_count,
            (SELECT count(*) FROM qep_outbox_events) AS outbox_count
        FROM qep_runs AS run
        JOIN qep_attempts AS attempt ON attempt.run_id = run.id
        WHERE run.id = 'run-1'
        """
    )
    assert row is not None
    return dict(row)


def _digest_hex(value) -> str:
    assert value is not None
    return value.value.removeprefix("sha256:")


def _prestart_command(*, expected_run_version: int = 0):
    closure_digest = d("d")
    intent_digest = d("c")
    fact_digest = d("b")
    original = OriginalItemResolution(
        source_kind=OriginalSourceKind.PRESTART_CANCEL,
        attempt_id=None,
        attempt_no=None,
        attempt_fence=None,
        attempt_item_set_digest=None,
        execution_fact=AttemptExecutionFact.CANCELLED,
        fact_schema="qep.prestart-cancel-item.v1",
        fact_version=1,
        fact_digest=fact_digest,
        evidence_root_digest=None,
        result_mapping_schema=None,
        result_mapping_version=None,
        result_mapping_digest=None,
        prestart_closure_digest=closure_digest,
    )
    effective = EffectiveItemResolution(
        source_kind=EffectiveSourceKind.ORIGINAL,
        attempt_id=None,
        attempt_no=None,
        attempt_fence=None,
        attempt_item_set_digest=None,
        outcome=RunOutcome.CANCELLED,
        fact_schema=original.fact_schema,
        fact_version=original.fact_version,
        fact_digest=fact_digest,
        evidence_root_digest=None,
        result_mapping_schema=None,
        result_mapping_version=None,
        result_mapping_digest=None,
        retry_intent_digest=None,
        retry_decision_digest=None,
        retry_authority_digest=None,
        adjudication_digest=None,
    )
    entry = RunItemResolution(
        item_key=RunItemKey("manifest-1", 0),
        original=original,
        effective=effective,
        unknown_lineage_digest=None,
        aggregation_class=ItemAggregationClass.CANCELLED,
    )
    resolved = RunItemResolutionSet.build(
        expected_item_keys=(entry.item_key,),
        entries=(entry,),
        batch_id="batch-1",
        run_id="run-1",
        source_run_version=0,
        manifest_digest=d("1"),
        shard_plan_digest=d("2"),
        run_item_set_digest=d("3"),
        attempt_chain_digest=d("5"),
        retry_chain_digest=None,
        adjudication_chain_digest=None,
    )
    candidate = bound_basis(
        resolved,
        original_attempt_id=None,
        original_attempt_fence=None,
        final_attempt_id=None,
        final_attempt_no=None,
        final_attempt_fence=None,
        final_attempt_version=None,
        final_attempt_state=None,
        worker_id=None,
        worker_generation=None,
        attempt_chain_digest=None,
        evidence_root_digest=None,
        cancellation_intent_digest=intent_digest,
        prestart_closure_digest=closure_digest,
        outcome=RunOutcome.CANCELLED,
        terminal_input_kind=TerminalInputKind.PRESTART_CANCEL,
    )
    return command(
        resolution_set=resolved,
        candidate_basis=candidate,
        expected_run_version=expected_run_version,
        expected_attempt_version=None,
    )


def _publication(
    finalize_command,
    authority: FinalizeRunAuthority,
    snapshot: RunFinalizationMutationSnapshot,
) -> RunFinalizationPublication:
    basis = finalize_command.candidate_basis
    state = RunFinalizationState(
        phase=RunPhase.CLOSED,
        disposition=RunDisposition.CLOSED_NO_RETRY,
        outcome=basis.outcome,
        finalization_basis_digest=basis.basis_digest,
        latest_attempt_fact=basis.final_attempt_state,
        current_assignment_id=None,
        pending_retry_intent_id=None,
    )
    projection = RunFinalizationProjection(
        run_id=basis.run_id,
        source_run_version=basis.source_run_version,
        state=state,
    )
    return RunFinalizationPublication(
        authority=authority,
        expected_snapshot=snapshot,
        basis=basis,
        resolution_set=finalize_command.resolution_set,
        projection=projection,
        side_effect=RunFinalizationSideEffect(
            run_id=basis.run_id,
            basis_digest=basis.basis_digest,
            outcome=basis.outcome,
        ),
        handoff=build_run_closed_handoff(
            basis=basis,
            resolution_set=finalize_command.resolution_set,
            authority_digest=authority.authority_digest,
            write_epoch=authority.write_epoch,
        ),
    )
