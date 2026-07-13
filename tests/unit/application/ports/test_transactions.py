"""T-M0-PORT-001 atomic application-unit-of-work contract."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tests.fakes.greenfield.transactions import (
    InMemoryApplicationState,
    InMemoryApplicationUnitOfWork,
)

from qarunner.application.ports.audit import AuditRecord
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.facts import FactKey, VersionedFactCommand
from qarunner.application.ports.transactions import ApplicationUnitOfWork
from qarunner.domain import (
    Digest,
    EvidenceConflict,
    IdempotencyRecord,
    PlatformExitClass,
    Run,
    RunState,
    TrustedExitFacts,
    WorkerRef,
    build_evidence_manifest,
)


async def test_committed_fact_state_requires_a_unit_of_work_for_mutation() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()

    with pytest.raises(PortContractError) as captured:
        await state.facts.commit(command)

    assert captured.value.resource == "application_state"
    assert captured.value.field == "facts"
    assert captured.value.reason == "transaction_required"
    assert await state.facts.get(command.key) is None


async def test_committed_evidence_state_requires_a_unit_of_work_for_mutation() -> None:
    state = InMemoryApplicationState()
    manifest = _infra_failed_manifest()

    with pytest.raises(PortContractError) as captured:
        await state.evidence.finalize(manifest)

    assert captured.value.resource == "application_state"
    assert captured.value.field == "evidence"
    assert captured.value.reason == "transaction_required"
    assert await state.evidence.get(manifest.attempt_id) is None


async def test_committed_audit_state_requires_a_unit_of_work_for_mutation() -> None:
    state = InMemoryApplicationState()
    audit = _audit_record()

    with pytest.raises(PortContractError) as captured:
        await state.audit.append(audit)

    assert captured.value.resource == "application_state"
    assert captured.value.field == "audit"
    assert captured.value.reason == "transaction_required"
    assert await state.audit.list_for_object("run", "run-1") == ()


async def test_unit_of_work_publishes_fact_evidence_and_audit_together() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()
    manifest = _infra_failed_manifest()
    audit = _audit_record()

    async with InMemoryApplicationUnitOfWork(state) as unit_of_work:
        assert isinstance(unit_of_work, ApplicationUnitOfWork)
        await unit_of_work.facts.commit(command)
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.audit.append(audit)

        assert await state.facts.get(command.key) is None
        assert await state.evidence.get(manifest.attempt_id) is None
        assert await state.audit.list_for_object("run", "run-1") == ()

        await unit_of_work.commit()

    assert await state.facts.get(command.key) == command.fact
    assert await state.evidence.get(manifest.attempt_id) == manifest
    assert await state.audit.list_for_object("run", "run-1") == (audit,)


async def test_unit_of_work_commit_failure_leaves_no_partial_facts() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()
    manifest = _infra_failed_manifest()
    audit = _audit_record()
    commit_error = RuntimeError("commit unavailable")

    with pytest.raises(RuntimeError, match="commit unavailable"):
        async with InMemoryApplicationUnitOfWork(state, commit_error=commit_error) as unit_of_work:
            await unit_of_work.facts.commit(command)
            await unit_of_work.evidence.finalize(manifest)
            await unit_of_work.audit.append(audit)
            await unit_of_work.commit()

    assert await state.facts.get(command.key) is None
    assert await state.evidence.get(manifest.attempt_id) is None
    assert await state.audit.list_for_object("run", "run-1") == ()


async def test_unit_of_work_cannot_commit_after_a_caught_participant_failure() -> None:
    state = InMemoryApplicationState()
    create = _create_run_command()
    manifest = _infra_failed_manifest()
    first_audit = _audit_record()
    async with InMemoryApplicationUnitOfWork(state) as initial:
        await initial.facts.commit(create)
        await initial.evidence.finalize(manifest)
        await initial.audit.append(first_audit)
        await initial.commit()

    queued = create.fact.transition(RunState.QUEUED, expected_version=0)
    update = VersionedFactCommand(
        key=create.key,
        expected_version=0,
        fact=queued,
        idempotency=IdempotencyRecord.create(
            scope="run:queue",
            key="queue-run-1",
            request_digest=Digest("sha256:" + "4" * 64),
            response_status=200,
            response_ref=queued.id,
        ),
    )
    conflicting_manifest = _infra_failed_manifest(run_id="run-2")
    second_audit = replace(
        first_audit,
        event_id="audit-2",
        action="queue",
        after_digest=Digest("sha256:" + "6" * 64),
    )

    async with InMemoryApplicationUnitOfWork(state) as unit_of_work:
        await unit_of_work.facts.commit(update)
        await unit_of_work.audit.append(second_audit)
        cached_audit = unit_of_work.audit
        with pytest.raises(EvidenceConflict):
            await unit_of_work.evidence.finalize(conflicting_manifest)

        with pytest.raises(PortContractError) as participant_error:
            await cached_audit.list_for_object("run", "run-1")

        assert participant_error.value.resource == "unit_of_work"
        assert participant_error.value.field == "state"
        assert participant_error.value.reason == "aborted"

        with pytest.raises(PortContractError) as captured:
            await unit_of_work.commit()

        assert captured.value.resource == "unit_of_work"
        assert captured.value.field == "state"
        assert captured.value.reason == "aborted"

    assert await state.facts.get(create.key) == create.fact
    assert await state.evidence.get(manifest.attempt_id) == manifest
    assert await state.audit.list_for_object("run", "run-1") == (first_audit,)


async def test_overlapping_units_of_work_cannot_overwrite_a_newer_commit() -> None:
    state = InMemoryApplicationState()
    first_command = _create_run_command()
    second_command = _create_run_command(run_id="run-2", digest_digit="7")

    async with (
        InMemoryApplicationUnitOfWork(state) as first,
        InMemoryApplicationUnitOfWork(state) as stale,
    ):
        await first.facts.commit(first_command)
        await stale.facts.commit(second_command)
        await first.commit()

        with pytest.raises(PortContractError) as captured:
            await stale.commit()

        assert captured.value.resource == "unit_of_work"
        assert captured.value.field == "state"
        assert captured.value.reason == "stale_snapshot"

    assert await state.facts.get(first_command.key) == first_command.fact
    assert await state.facts.get(second_command.key) is None


async def test_unit_of_work_closes_participants_immediately_after_commit() -> None:
    state = InMemoryApplicationState()
    audit = _audit_record()
    unit_of_work = InMemoryApplicationUnitOfWork(state)

    async with unit_of_work:
        cached_audit = unit_of_work.audit
        await cached_audit.append(audit)
        await unit_of_work.commit()

        with pytest.raises(PortContractError) as cached_error:
            await cached_audit.append(replace(audit, event_id="audit-via-cached-participant"))

        assert cached_error.value.reason == "closed"

        with pytest.raises(PortContractError) as captured:
            await unit_of_work.audit.append(replace(audit, event_id="audit-after-commit"))

        assert captured.value.resource == "unit_of_work"
        assert captured.value.field == "state"
        assert captured.value.reason == "closed"

    assert await state.audit.list_for_object("run", "run-1") == (audit,)


async def test_unit_of_work_closes_cached_fact_and_evidence_participants() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()
    manifest = _infra_failed_manifest()

    async with InMemoryApplicationUnitOfWork(state) as unit_of_work:
        cached_facts = unit_of_work.facts
        cached_evidence = unit_of_work.evidence
        await cached_facts.commit(command)
        await cached_evidence.finalize(manifest)
        await unit_of_work.commit()

        with pytest.raises(PortContractError) as fact_error:
            await cached_facts.get(command.key)
        with pytest.raises(PortContractError) as evidence_error:
            await cached_evidence.get(manifest.attempt_id)

        assert fact_error.value.reason == "closed"
        assert evidence_error.value.reason == "closed"

    assert await state.facts.get(command.key) == command.fact
    assert await state.evidence.get(manifest.attempt_id) == manifest


async def test_explicit_rollback_discards_all_staged_application_facts() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()
    manifest = _infra_failed_manifest()
    audit = _audit_record()

    async with InMemoryApplicationUnitOfWork(state) as unit_of_work:
        await unit_of_work.facts.commit(command)
        await unit_of_work.evidence.finalize(manifest)
        await unit_of_work.audit.append(audit)

        await unit_of_work.rollback()

        with pytest.raises(PortContractError) as captured:
            await unit_of_work.commit()
        assert captured.value.reason == "closed"

    assert await state.facts.get(command.key) is None
    assert await state.evidence.get(manifest.attempt_id) is None
    assert await state.audit.list_for_object("run", "run-1") == ()


async def test_unit_of_work_body_exception_discards_all_staged_application_facts() -> None:
    state = InMemoryApplicationState()
    command = _create_run_command()
    manifest = _infra_failed_manifest()
    audit = _audit_record()

    with pytest.raises(RuntimeError, match="application command failed"):
        async with InMemoryApplicationUnitOfWork(state) as unit_of_work:
            await unit_of_work.facts.commit(command)
            await unit_of_work.evidence.finalize(manifest)
            await unit_of_work.audit.append(audit)
            raise RuntimeError("application command failed")

    assert await state.facts.get(command.key) is None
    assert await state.evidence.get(manifest.attempt_id) is None
    assert await state.audit.list_for_object("run", "run-1") == ()


async def test_unit_of_work_is_one_shot_and_cannot_be_reentered() -> None:
    state = InMemoryApplicationState()
    unit_of_work = InMemoryApplicationUnitOfWork(state)

    async with unit_of_work:
        with pytest.raises(PortContractError) as active_error:
            await unit_of_work.__aenter__()
        assert active_error.value.reason == "already_active"

        await unit_of_work.commit()

        with pytest.raises(PortContractError) as commit_error:
            await unit_of_work.commit()
        assert commit_error.value.reason == "closed"

    with pytest.raises(PortContractError) as reentry_error:
        await unit_of_work.__aenter__()
    assert reentry_error.value.reason == "closed"


def _create_run_command(
    *, run_id: str = "run-1", digest_digit: str = "1"
) -> VersionedFactCommand[Run]:
    run = Run.create(run_id=run_id)
    return VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key=f"create-{run_id}",
            request_digest=Digest("sha256:" + digest_digit * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )


def _infra_failed_manifest(*, run_id: str = "run-1"):
    return build_evidence_manifest(
        attempt_id="attempt-1",
        run_id=run_id,
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=WorkerRef(worker_id="worker-1", generation=1),
        execution_spec_digest=Digest("sha256:" + "2" * 64),
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
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        source="finalizer",
    )
