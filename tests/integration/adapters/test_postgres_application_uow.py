"""Real PostgreSQL coverage for the aggregate application transaction port."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.ports.audit import AuditRecord
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.facts import FactKey, VersionedFactCommand
from qarunner.application.ports.transactions import ApplicationUnitOfWork
from qarunner.domain import (
    ArtifactClass,
    ArtifactPath,
    Digest,
    IdempotencyRecord,
    PlatformExitClass,
    Run,
    RunState,
    TrustedCancellationStop,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    WorkerRef,
    build_evidence_manifest,
)
from qarunner.domain.evidence import EvidenceOutcome


class _RunCodec:
    schema_version = "qep.test-run-fact.v1"

    def encode(self, fact: Run) -> dict[str, object]:
        return {
            "id": fact.id,
            "state": fact.state.value,
            "version": fact.version,
            "current_fence": fact.current_fence,
            "assignments": [],
            "current_assignment_id": fact.current_assignment_id,
            "attempts": [],
            "retry_intents": [],
            "pending_retry_intent_id": fact.pending_retry_intent_id,
            "cancel_intent": None,
            "cancellation_stop": None,
        }

    def decode(self, payload: dict[str, object]) -> Run:
        return Run(
            id=str(payload["id"]),
            state=RunState(str(payload["state"])),
            version=int(payload["version"]),
            current_fence=int(payload["current_fence"]),
            assignments=(),
            current_assignment_id=None,
            attempts=(),
            retry_intents=(),
            pending_retry_intent_id=None,
            cancel_intent=None,
            cancellation_stop=None,
        )


class _InvalidSchemaRunCodec(_RunCodec):
    schema_version = "   "


class _NonObjectRunCodec(_RunCodec):
    def encode(self, fact: Run) -> dict[str, object]:
        return cast(dict[str, object], [])


class _MismatchedRunCodec(_RunCodec):
    def decode(self, payload: dict[str, object]) -> Run:
        return replace(super().decode(payload), state=RunState.QUEUED)


@pytest.fixture
async def application_uow_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-application-uow-admin-password-at-least-32-chars",
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
async def test_application_uow_atomically_publishes_fact_evidence_and_audit(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    command = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    manifest = _manifest()
    audit = _audit_record()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        assert isinstance(unit_of_work, ApplicationUnitOfWork)
        await unit_of_work.facts.commit(command)
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.audit.append(audit)

        async with factory() as observer:
            assert await observer.facts.get(key) is None
            assert await observer.evidence.get(manifest.attempt_id) is None
            assert await observer.audit.list_for_object("run", run.id) == ()
            await observer.rollback()

        await unit_of_work.commit()

    async with factory() as reader:
        assert await reader.facts.get(key) == run
        assert await reader.evidence.get(manifest.attempt_id) == manifest
        assert await reader.audit.list_for_object("run", run.id) == (audit,)
        await reader.rollback()


@pytest.mark.asyncio
async def test_fact_exact_replay_precedes_current_version_validation(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    create = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    queued = run.transition(RunState.QUEUED, expected_version=0)
    update = VersionedFactCommand(
        key=key,
        expected_version=0,
        fact=queued,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "2" * 64),
            response_status=200,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(create)
        await unit_of_work.facts.commit(update)
        await unit_of_work.commit()

    async with factory() as unit_of_work:
        replay = await unit_of_work.facts.commit(create)
        assert replay.fact == run
        assert replay.replayed is True
        assert await unit_of_work.facts.get(key) == queued
        await unit_of_work.rollback()


@pytest.mark.asyncio
async def test_caught_participant_failure_sticky_aborts_and_rolls_back(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    command = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    audit = _audit_record()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(command)
        await unit_of_work.audit.append(audit)
        with pytest.raises(PortContractError) as conflict:
            await unit_of_work.audit.append(
                replace(audit, decision="denied", reason="policy_denied")
            )
        assert conflict.value.reason == "conflicting_content"

        with pytest.raises(PortContractError) as aborted:
            await unit_of_work.commit()
        assert aborted.value.reason == "aborted"

    async with factory() as reader:
        assert await reader.facts.get(key) is None
        assert await reader.audit.list_for_object("run", run.id) == ()
        await reader.rollback()


@pytest.mark.asyncio
async def test_evidence_index_round_trips_a_cancellation_stop_manifest(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _cancelled_manifest()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        result = await unit_of_work.evidence.finalize(manifest)
        assert result.value == manifest
        assert result.replayed is False
        await unit_of_work.commit()

    async with factory() as reader:
        assert await reader.evidence.get(manifest.attempt_id) == manifest
        await reader.rollback()


@pytest.mark.asyncio
async def test_evidence_index_replays_exactly_and_rejects_a_conflicting_manifest(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.domain.errors import EvidenceConflict

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _manifest()
    conflicting = build_evidence_manifest(
        attempt_id=manifest.attempt_id,
        run_id=manifest.run_id,
        attempt_no=manifest.attempt_no,
        assignment_id=manifest.assignment_id,
        fence=manifest.fence,
        worker=manifest.worker,
        execution_spec_digest=Digest("sha256:" + "9" * 64),
        trusted_exit=manifest.platform_exit,
        case_summary=manifest.case_summary,
        artifacts=manifest.artifacts,
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        first = await unit_of_work.evidence.finalize(manifest)
        assert first == ReplayResult(value=manifest, replayed=False)
        await unit_of_work.commit()
    async with factory() as replay_uow:
        replay = await replay_uow.evidence.finalize(manifest)
        assert replay == ReplayResult(value=manifest, replayed=True)
        await replay_uow.rollback()
    with pytest.raises(EvidenceConflict):
        async with factory() as conflict_uow:
            await conflict_uow.evidence.finalize(conflicting)


@pytest.mark.asyncio
async def test_fact_cas_rejects_a_corrupt_current_snapshot(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    create = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    queued = run.transition(RunState.QUEUED, expected_version=0)
    update = VersionedFactCommand(
        key=key,
        expected_version=0,
        fact=queued,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "2" * 64),
            response_status=200,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(create)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await connection.execute("UPDATE qep_versioned_fact_snapshots SET payload = '{}'::jsonb")

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as unit_of_work:
            await unit_of_work.facts.commit(update)

    assert corrupt.value.resource == "fact_store"
    assert corrupt.value.field == "stored_fact"
    assert corrupt.value.reason == "integrity_invalid"
    async with pool.acquire() as connection:
        versions = await connection.fetch(
            "SELECT version FROM qep_versioned_fact_snapshots ORDER BY version"
        )
    assert [row["version"] for row in versions] == [0]


@pytest.mark.asyncio
async def test_fact_idempotency_conflict_does_not_overwrite_the_first_snapshot(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.domain.errors import IdempotencyConflict

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    first = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    conflict = replace(
        first,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "9" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(first)
        await unit_of_work.commit()
    with pytest.raises(IdempotencyConflict):
        async with factory() as unit_of_work:
            await unit_of_work.facts.commit(conflict)
    async with factory() as reader:
        assert await reader.facts.get(key) == run
        await reader.rollback()


@pytest.mark.asyncio
async def test_evidence_index_rejects_non_boolean_platform_flags(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    forged = build_evidence_manifest(
        attempt_id="attempt-1",
        run_id="run-1",
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=WorkerRef(worker_id="worker-1", generation=1),
        execution_spec_digest=Digest("sha256:" + "1" * 64),
        trusted_exit=TrustedExitFacts(
            source_event_id="event-exit-1",
            pid=None,
            exit_class=PlatformExitClass.INFRA_FAILED,
            exit_code=None,
            signal=None,
            oom=1,
            timeout=False,
        ),
        case_summary=None,
        artifacts=(),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.evidence.finalize(forged)

    assert invalid.value.resource == "evidence_index"
    assert invalid.value.field == "manifest"
    assert invalid.value.reason == "not_domain_built"


@pytest.mark.asyncio
async def test_evidence_index_rejects_a_manifest_with_forged_derived_outcome(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    forged = replace(_manifest(), outcome=EvidenceOutcome.PASSED)

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.evidence.finalize(forged)

    assert invalid.value.resource == "evidence_index"
    assert invalid.value.field == "manifest"
    assert invalid.value.reason == "not_domain_built"


@pytest.mark.asyncio
async def test_evidence_index_rejects_a_non_summary_object(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    forged = replace(
        _manifest(),
        case_summary=cast(ValidatedCaseSummary, object()),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.evidence.finalize(forged)

    assert invalid.value.resource == "evidence_index"
    assert invalid.value.field == "manifest"
    assert invalid.value.reason == "not_domain_built"


@pytest.mark.asyncio
async def test_evidence_index_rejects_noncanonical_stored_payload(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _manifest()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_evidence_index
            SET payload = payload || '{"extra":"forged"}'::jsonb
            """
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.evidence.get(manifest.attempt_id)

    assert corrupt.value.resource == "evidence_index"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_evidence_index_rejects_a_corrupt_storage_envelope(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _manifest()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_evidence_index SET root_digest = $1",
            "9" * 64,
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.evidence.get(manifest.attempt_id)

    assert corrupt.value.resource == "evidence_index"
    assert corrupt.value.field == "stored_manifest"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_audit_log_rejects_noncanonical_stored_payload(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    audit = _audit_record()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.audit.append(audit)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_audit_events
            SET payload = payload || '{"extra":"forged"}'::jsonb
            """
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.audit.list_for_object("run", "run-1")

    assert corrupt.value.resource == "audit_log"
    assert corrupt.value.field == "stored_record"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_audit_log_coexists_with_specialized_audit_payloads(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    audit = _audit_record()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, NULL, $8, $9, $10)
            """,
            "run-finalization-audit-existing",
            "system",
            "finalize_run",
            "run",
            "run-1",
            "allowed",
            "run_finalization_basis_committed",
            "2" * 64,
            json.dumps({"schema_version": "qep.run-finalization-audit.v1"}),
            audit.occurred_at - timedelta(seconds=1),
        )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.audit.append(audit)
        await unit_of_work.commit()
    async with factory() as reader:
        assert await reader.audit.list_for_object("run", "run-1") == (audit,)
        await reader.rollback()


@pytest.mark.asyncio
async def test_audit_log_replays_exactly_and_rejects_conflicting_content(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    audit = _audit_record()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        first = await unit_of_work.audit.append(audit)
        assert first == ReplayResult(value=audit, replayed=False)
        await unit_of_work.commit()
    async with factory() as replay_uow:
        replay = await replay_uow.audit.append(audit)
        assert replay == ReplayResult(value=audit, replayed=True)
        await replay_uow.rollback()
    with pytest.raises(PortContractError) as conflict:
        async with factory() as conflict_uow:
            await conflict_uow.audit.append(
                replace(audit, decision="denied", reason="policy_denied")
            )

    assert conflict.value.resource == "audit_log"
    assert conflict.value.field == "event_id"
    assert conflict.value.reason == "conflicting_content"
    async with factory() as reader:
        assert await reader.audit.list_for_object("run", "run-1") == (audit,)
        await reader.rollback()


@pytest.mark.asyncio
async def test_audit_log_rejects_malformed_existing_event_payloads(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    variants = (
        ("wrong-schema", {"schema_version": "qep.other-audit.v1"}),
        ("missing-fields", {"schema_version": "qep.application-audit.v1"}),
        ("non-object", ["forged"]),
    )
    async with pool.acquire() as connection:
        for suffix, payload in variants:
            await connection.execute(
                """
                INSERT INTO qep_audit_events (
                    id, actor_id, action, object_type, object_id, decision, reason_code,
                    before_digest, after_digest, payload, occurred_at
                ) VALUES ($1, 'system', 'queue', 'run', 'run-1', 'allowed', 'forged',
                          NULL, NULL, $2, $3)
                """,
                f"forged-{suffix}",
                json.dumps(payload),
                datetime(2026, 7, 19, 12, tzinfo=UTC),
            )

    for suffix, _ in variants:
        record = replace(_audit_record(), event_id=f"forged-{suffix}")
        with pytest.raises(PortContractError) as corrupt:
            async with PostgresApplicationUnitOfWork(
                pool,
                fact_codecs={"run": _RunCodec()},
            ) as unit_of_work:
                await unit_of_work.audit.append(record)

        assert corrupt.value.resource == "audit_log"
        assert corrupt.value.field == "stored_record"
        assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_fact_decoder_rejects_payload_fields_unknown_to_the_codec(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError
    from qarunner.domain.digest import canonical_digest

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    command = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(command)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        payload = json.loads(
            await connection.fetchval("SELECT payload FROM qep_versioned_fact_snapshots")
        )
        payload["extra"] = "forged"
        digest = canonical_digest(
            schema_version=_RunCodec.schema_version,
            payload=payload,
        )
        await connection.execute(
            "UPDATE qep_versioned_fact_snapshots SET payload = $1, payload_digest = $2",
            json.dumps(payload, sort_keys=True),
            digest.value.removeprefix("sha256:"),
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.facts.get(key)

    assert corrupt.value.resource == "fact_store"
    assert corrupt.value.field == "stored_fact"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_evidence_index_round_trips_a_passed_manifest_with_artifacts(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    artifact = VerifiedArtifact(
        path=ArtifactPath("case-results.json"),
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=Digest("sha256:" + "5" * 64),
    )
    manifest = build_evidence_manifest(
        attempt_id="attempt-1",
        run_id="run-1",
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=WorkerRef(worker_id="worker-1", generation=1),
        execution_spec_digest=Digest("sha256:" + "1" * 64),
        trusted_exit=TrustedExitFacts(
            source_event_id="event-exit-passed",
            pid=731,
            exit_class=PlatformExitClass.COMPLETED,
            exit_code=0,
            signal=None,
            oom=False,
            timeout=False,
        ),
        case_summary=ValidatedCaseSummary(
            schema_version="qep.case-summary.v1",
            source_artifact_path=artifact.path,
            source_artifact_digest=artifact.digest,
            expected=1,
            passed=1,
            failed=0,
            skipped=0,
            not_reported=0,
            unexpected=0,
        ),
        artifacts=(artifact,),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        result = await unit_of_work.evidence.finalize(manifest)
        assert result.value == manifest
        await unit_of_work.commit()
    async with factory() as reader:
        assert await reader.evidence.get(manifest.attempt_id) == manifest
        await reader.rollback()


@pytest.mark.asyncio
async def test_evidence_index_rejects_a_corrupt_cancellation_proof_digest(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _cancelled_manifest()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        payload = json.loads(await connection.fetchval("SELECT payload FROM qep_evidence_index"))
        payload["cancellation_stop"]["proof_digest"] = "sha256:" + "9" * 64
        await connection.execute(
            "UPDATE qep_evidence_index SET payload = $1",
            json.dumps(payload, sort_keys=True),
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.evidence.get(manifest.attempt_id)

    assert corrupt.value.resource == "evidence_index"
    assert corrupt.value.field == "stored_manifest"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_evidence_index_rejects_a_non_string_cancellation_timestamp(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _cancelled_manifest()

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        payload = json.loads(await connection.fetchval("SELECT payload FROM qep_evidence_index"))
        payload["cancellation_stop"]["process_stopped_at"] = 1
        await connection.execute(
            "UPDATE qep_evidence_index SET payload = $1",
            json.dumps(payload, sort_keys=True),
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as reader:
            await reader.evidence.get(manifest.attempt_id)

    assert corrupt.value.resource == "evidence_index"
    assert corrupt.value.field == "stored_manifest"
    assert corrupt.value.reason == "integrity_invalid"


@pytest.mark.asyncio
async def test_fact_commit_rejects_a_key_bound_to_a_different_fact_id(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    fact = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value="run-other"),
        expected_version=None,
        fact=fact,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="wrong-key",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=fact.id,
        ),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert invalid.value.resource == "fact_store"
    assert invalid.value.field == "key.value"
    assert invalid.value.reason == "fact_id_mismatch"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 0


@pytest.mark.asyncio
async def test_fact_store_rejects_an_unregistered_fact_kind(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    async with PostgresApplicationUnitOfWork(pool, fact_codecs={}) as unit_of_work:
        with pytest.raises(PortContractError) as unsupported:
            await unit_of_work.facts.get(FactKey(kind="run", value="run-1"))

    assert unsupported.value.resource == "fact_store"
    assert unsupported.value.field == "key.kind"
    assert unsupported.value.reason == "unsupported"


@pytest.mark.asyncio
async def test_fact_store_rejects_a_codec_without_a_schema_version(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    async with PostgresApplicationUnitOfWork(
        pool,
        fact_codecs={"run": _InvalidSchemaRunCodec()},
    ) as unit_of_work:
        with pytest.raises(PortContractError) as invalid:
            await unit_of_work.facts.get(FactKey(kind="run", value="run-1"))

    assert invalid.value.resource == "fact_store"
    assert invalid.value.field == "codec"
    assert invalid.value.reason == "invalid"


@pytest.mark.asyncio
async def test_fact_store_rejects_a_codec_that_encodes_a_non_object(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _NonObjectRunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert invalid.value.resource == "fact_store"
    assert invalid.value.field == "codec"
    assert invalid.value.reason == "invalid_payload"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 0


@pytest.mark.asyncio
async def test_fact_store_rejects_a_codec_round_trip_mismatch(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _MismatchedRunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert invalid.value.resource == "fact_store"
    assert invalid.value.field == "codec"
    assert invalid.value.reason == "round_trip_mismatch"


@pytest.mark.asyncio
async def test_fact_commit_rejects_a_second_create_for_an_existing_key(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    first = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    second = replace(
        first,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-2",
            request_digest=Digest("sha256:" + "2" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(first)
        await unit_of_work.commit()
    with pytest.raises(PortContractError) as duplicate:
        async with factory() as unit_of_work:
            await unit_of_work.facts.commit(second)
    assert duplicate.value.reason == "already_exists"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 1


@pytest.mark.asyncio
async def test_fact_commit_rejects_an_update_for_a_missing_key(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    fact = Run.create(run_id="run-1").transition(RunState.QUEUED, expected_version=0)
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=fact.id),
        expected_version=0,
        fact=fact,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=200,
            response_ref=fact.id,
        ),
    )

    with pytest.raises(PortContractError) as missing:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert missing.value.resource == "fact_store"
    assert missing.value.field == "key"
    assert missing.value.reason == "not_found"


@pytest.mark.asyncio
async def test_fact_commit_rejects_a_stale_expected_version(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.domain.errors import VersionConflict

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    create = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    queued = run.transition(RunState.QUEUED, expected_version=0)
    stale = VersionedFactCommand(
        key=key,
        expected_version=1,
        fact=queued,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "2" * 64),
            response_status=200,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(create)
        await unit_of_work.commit()
    with pytest.raises(VersionConflict) as conflict:
        async with factory() as unit_of_work:
            await unit_of_work.facts.commit(stale)
    assert (conflict.value.current_version, conflict.value.expected_version) == (0, 1)
    async with factory() as reader:
        assert await reader.facts.get(key) == run
        await reader.rollback()


@pytest.mark.asyncio
async def test_fact_commit_rejects_a_nonzero_create_version(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    fact = replace(Run.create(run_id="run-1"), version=1)
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=fact.id),
        expected_version=None,
        fact=fact,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=fact.id,
        ),
    )

    with pytest.raises(PortContractError) as invalid:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert invalid.value.resource == "fact_store"
    assert invalid.value.field == "fact.version"
    assert invalid.value.reason == "not_next_version"


@pytest.mark.asyncio
async def test_fact_replay_rejects_same_digest_with_different_command_content(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    idempotency = IdempotencyRecord.create(
        scope="run:create",
        key="create-run-1",
        request_digest=Digest("sha256:" + "1" * 64),
        response_status=201,
        response_ref=run.id,
    )
    first = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=idempotency,
    )
    changed = VersionedFactCommand(
        key=key,
        expected_version=0,
        fact=run.transition(RunState.QUEUED, expected_version=0),
        idempotency=idempotency,
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(first)
        await unit_of_work.commit()
    with pytest.raises(PortContractError) as conflict:
        async with factory() as unit_of_work:
            await unit_of_work.facts.commit(changed)

    assert conflict.value.resource == "fact_store"
    assert conflict.value.field == "idempotency"
    assert conflict.value.reason == "non_exact_replay"


@pytest.mark.asyncio
async def test_fact_replay_rejects_an_orphaned_command_result(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    command = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(command)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await _drop_fact_command_foreign_key(connection)
        await connection.execute("DELETE FROM qep_versioned_fact_snapshots")

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as replay_uow:
            await replay_uow.facts.commit(command)

    assert corrupt.value.resource == "fact_store"
    assert corrupt.value.field == "idempotency"
    assert corrupt.value.reason == "stored_result_missing"


@pytest.mark.asyncio
async def test_fact_replay_rejects_a_missing_historical_snapshot(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    create = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    queued = run.transition(RunState.QUEUED, expected_version=0)
    update = VersionedFactCommand(
        key=key,
        expected_version=0,
        fact=queued,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "2" * 64),
            response_status=200,
            response_ref=run.id,
        ),
    )

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as unit_of_work:
        await unit_of_work.facts.commit(create)
        await unit_of_work.facts.commit(update)
        await unit_of_work.commit()
    async with pool.acquire() as connection:
        await _drop_fact_command_foreign_key(connection)
        await connection.execute(
            "DELETE FROM qep_versioned_fact_snapshots WHERE fact_key = $1 AND version = 0",
            run.id,
        )

    with pytest.raises(PortContractError) as corrupt:
        async with factory() as replay_uow:
            await replay_uow.facts.commit(create)

    assert corrupt.value.resource == "fact_store"
    assert corrupt.value.field == "idempotency"
    assert corrupt.value.reason == "stored_result_missing"


@pytest.mark.asyncio
async def test_suppressed_fact_insert_sticky_aborts_and_rolls_back(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_fact_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_fact_insert_trigger
            BEFORE INSERT ON qep_versioned_fact_snapshots
            FOR EACH ROW EXECUTE FUNCTION suppress_fact_insert()
            """
        )
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    with pytest.raises(PortContractError) as missing:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert missing.value.resource == "fact_store"
    assert missing.value.reason == "write_missing"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 0
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_commands") == 0


@pytest.mark.asyncio
async def test_suppressed_fact_command_insert_rolls_back_the_snapshot(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_fact_command_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_fact_command_insert_trigger
            BEFORE INSERT ON qep_versioned_fact_commands
            FOR EACH ROW EXECUTE FUNCTION suppress_fact_command_insert()
            """
        )
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    with pytest.raises(PortContractError) as missing:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)

    assert missing.value.resource == "fact_store"
    assert missing.value.reason == "write_missing"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 0
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_commands") == 0


@pytest.mark.asyncio
async def test_suppressed_evidence_insert_rolls_back_staged_fact(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_evidence_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_evidence_insert_trigger
            BEFORE INSERT ON qep_evidence_index
            FOR EACH ROW EXECUTE FUNCTION suppress_evidence_insert()
            """
        )
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )

    with pytest.raises(PortContractError) as missing:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.facts.commit(command)
            await unit_of_work.evidence.finalize(_manifest())

    assert missing.value.resource == "evidence_index"
    assert missing.value.reason == "write_missing"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_versioned_fact_snapshots") == 0
        assert await connection.fetchval("SELECT count(*) FROM qep_evidence_index") == 0


@pytest.mark.asyncio
async def test_suppressed_audit_insert_aborts_the_application_transaction(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError

    pool = application_uow_store._require_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_audit_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_audit_insert_trigger
            BEFORE INSERT ON qep_audit_events
            FOR EACH ROW EXECUTE FUNCTION suppress_audit_insert()
            """
        )

    with pytest.raises(PortContractError) as missing:
        async with PostgresApplicationUnitOfWork(
            pool,
            fact_codecs={"run": _RunCodec()},
        ) as unit_of_work:
            await unit_of_work.audit.append(_audit_record())

    assert missing.value.resource == "audit_log"
    assert missing.value.reason == "write_missing"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_audit_events") == 0


@pytest.mark.asyncio
async def test_concurrent_identical_fact_writes_commit_exactly_once(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
    from qarunner.application.ports.common import PortContractError
    from qarunner.application.ports.facts import FactCommitResult

    pool = application_uow_store._require_pool()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    command = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def create_fact():
        await start.wait()
        async with PostgresApplicationUnitOfWork(
            pool, fact_codecs={"run": _RunCodec()}
        ) as unit_of_work:
            result = await unit_of_work.facts.commit(command)
            await unit_of_work.commit()
            return result

    results = await asyncio.gather(
        *(create_fact() for _ in range(contenders)), return_exceptions=True
    )

    # A truly concurrent "create" race can settle two different ways for a loser,
    # depending on whether it observes the winner's row before or after the
    # winner's INSERT commits: it either loses the composite-key INSERT race
    # outright (raw asyncpg.UniqueViolationError), or its own CAS pre-check
    # observes the now-committed row and fails closed with the domain-level
    # "already_exists" contract error. Both are losers; only one commit wins.
    winners = [result for result in results if isinstance(result, FactCommitResult)]
    losers = [
        result
        for result in results
        if isinstance(result, asyncpg.UniqueViolationError)
        or (isinstance(result, PortContractError) and result.reason == "already_exists")
    ]
    assert len(winners) + len(losers) == contenders
    assert len(winners) >= 1
    assert all(winner.fact == run for winner in winners)
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_versioned_fact_snapshots) AS snapshot_count,
                (SELECT count(*) FROM qep_versioned_fact_commands) AS command_count
            """
        )
    assert dict(persisted) == {"snapshot_count": 1, "command_count": 1}

    def factory() -> PostgresApplicationUnitOfWork:
        return PostgresApplicationUnitOfWork(pool, fact_codecs={"run": _RunCodec()})

    async with factory() as reader:
        assert await reader.facts.get(key) == run
        await reader.rollback()


@pytest.mark.asyncio
async def test_concurrent_identical_evidence_index_writes_commit_exactly_once(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    await _seed_attempt(pool)
    manifest = _manifest()
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def finalize_evidence():
        await start.wait()
        async with PostgresApplicationUnitOfWork(
            pool, fact_codecs={"run": _RunCodec()}
        ) as unit_of_work:
            result = await unit_of_work.evidence.finalize(_manifest())
            await unit_of_work.commit()
            return result

    results = await asyncio.gather(
        *(finalize_evidence() for _ in range(contenders)), return_exceptions=True
    )

    winners = [result for result in results if isinstance(result, ReplayResult)]
    conflicts = [result for result in results if isinstance(result, asyncpg.UniqueViolationError)]
    assert len(winners) + len(conflicts) == contenders
    assert len(winners) >= 1
    assert all(winner.value == manifest for winner in winners)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_evidence_index") == 1


@pytest.mark.asyncio
async def test_concurrent_identical_audit_writes_commit_exactly_once(
    application_uow_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork

    pool = application_uow_store._require_pool()
    audit = _audit_record()
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def append_audit():
        await start.wait()
        async with PostgresApplicationUnitOfWork(
            pool, fact_codecs={"run": _RunCodec()}
        ) as unit_of_work:
            result = await unit_of_work.audit.append(_audit_record())
            await unit_of_work.commit()
            return result

    results = await asyncio.gather(
        *(append_audit() for _ in range(contenders)), return_exceptions=True
    )

    winners = [result for result in results if isinstance(result, ReplayResult)]
    conflicts = [result for result in results if isinstance(result, asyncpg.UniqueViolationError)]
    assert len(winners) + len(conflicts) == contenders
    assert len(winners) >= 1
    assert all(winner.value == audit for winner in winners)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_audit_events") == 1


async def _seed_attempt(pool: asyncpg.Pool) -> None:
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
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
            "INSERT INTO qep_suites (id, project_id, name, created_at) VALUES ($1, $2, $3, $4)",
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
            "a" * 64,
            "b" * 64,
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
            "batch-1",
            "project-1",
            "suite-revision-1",
            "c" * 64,
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
            "batch-1",
            "qep.case-manifest.v1",
            "d" * 64,
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
            "batch-1",
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
            ) VALUES ($1, $2, $3, 0, $4, 'planned', 0, 0, 0, $5, $6, $6, $7)
            """,
            "run-1",
            "batch-1",
            "plan-1",
            "profile-1",
            "f" * 64,
            now,
            empty,
        )
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
            "0" * 64,
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
            "run-1",
            "worker-1",
            "1" * 64,
            "2" * 64,
            now,
            later,
            "attempt-1",
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
            "attempt-1",
            "run-1",
            "assignment-1",
            "worker-1",
            "1" * 64,
            "start-attempt-1",
            now,
            empty,
        )


async def _drop_fact_command_foreign_key(connection: asyncpg.Connection) -> None:
    constraint = await connection.fetchval(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'qep_versioned_fact_commands'::regclass
          AND contype = 'f'
        """
    )
    assert isinstance(constraint, str)
    quoted = constraint.replace('"', '""')
    await connection.execute(f'ALTER TABLE qep_versioned_fact_commands DROP CONSTRAINT "{quoted}"')


def _manifest():
    return build_evidence_manifest(
        attempt_id="attempt-1",
        run_id="run-1",
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=WorkerRef(worker_id="worker-1", generation=1),
        execution_spec_digest=Digest("sha256:" + "1" * 64),
        trusted_exit=TrustedExitFacts(
            source_event_id="event-exit-1",
            pid=None,
            exit_class=PlatformExitClass.INFRA_FAILED,
            exit_code=None,
            signal=None,
            oom=False,
            timeout=False,
        ),
        case_summary=None,
        artifacts=(),
    )


def _cancelled_manifest():
    worker = WorkerRef(worker_id="worker-1", generation=1)
    stopped_at = datetime(2026, 7, 19, 12, 1, tzinfo=UTC)
    stop = TrustedCancellationStop(
        run_id="run-1",
        attempt_id="attempt-1",
        fence=1,
        worker=worker,
        cancellation_intent_digest=Digest("sha256:" + "4" * 64),
        source_event_id="event-exit-cancelled",
        process_stopped_at=stopped_at,
        sut_access_stopped_at=stopped_at,
        recorded_at=datetime(2026, 7, 19, 12, 2, tzinfo=UTC),
    )
    return build_evidence_manifest(
        attempt_id="attempt-1",
        run_id="run-1",
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=worker,
        execution_spec_digest=Digest("sha256:" + "1" * 64),
        trusted_exit=TrustedExitFacts(
            source_event_id="event-exit-cancelled",
            pid=None,
            exit_class=PlatformExitClass.CANCELLED,
            exit_code=None,
            signal=None,
            oom=False,
            timeout=False,
        ),
        case_summary=None,
        artifacts=(),
        cancellation_stop=stop,
    )


def _audit_record() -> AuditRecord:
    return AuditRecord(
        event_id="audit-1",
        actor_id="system",
        authentication_strength="workload-identity",
        request_id="request-1",
        object_kind="run",
        object_id="run-1",
        action="finalize_evidence",
        decision="allowed",
        reason="trusted_evidence_complete",
        before_digest=None,
        after_digest=Digest("sha256:" + "3" * 64),
        occurred_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
        source="finalizer",
    )
