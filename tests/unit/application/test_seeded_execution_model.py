"""T-M0-MODEL-001 seeded execution-authority model tests."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.greenfield.facts import InMemoryVersionedFactStore

from qarunner.application.ports.facts import FactKey, VersionedFactCommand
from qarunner.domain import (
    AttemptAuthority,
    AttemptEvent,
    AttemptState,
    EvidenceManifest,
    EvidenceProposal,
    EvidenceRequirements,
    FinalizeAttemptEvidenceResult,
    IdempotencyRecord,
    PlatformExitClass,
    Run,
    RunState,
    TrustedExitFacts,
    VersionConflict,
    WorkerAuthority,
    WorkerGeneration,
    WorkerRef,
    WorkerState,
    build_evidence_manifest,
    canonical_digest,
)

REGISTERED_AT = datetime(2026, 7, 13, 12, tzinfo=UTC)
OFFERED_AT = REGISTERED_AT + timedelta(minutes=1)
CLAIMED_AT = OFFERED_AT + timedelta(minutes=1)
COMMITTED_AT = CLAIMED_AT + timedelta(minutes=1)
EXPIRES_AT = OFFERED_AT + timedelta(hours=1)


async def test_run_owned_event_is_persisted_as_one_monotonic_fact() -> None:
    """A current Attempt event advances the child and its persisted Run aggregate."""
    store, committed, worker = await _persisted_committed_run()
    current_attempt = committed.attempts[-1]
    event = AttemptEvent(
        event_id="event-model-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )

    updated = committed.record_current_attempt_event(
        attempt_id=current_attempt.id,
        event=event,
        authority=AttemptAuthority(
            current_fence=committed.current_fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=committed.current_fence,
        expected_version=committed.version,
        expected_attempt_version=current_attempt.version,
    )
    await _persist(
        store,
        updated,
        previous_version=committed.version,
        action="record-attempt-event",
    )

    assert updated.version == committed.version + 1
    assert updated.attempts[-1].version == current_attempt.version + 1
    assert updated.attempts[-1].events == (event,)
    assert committed.attempts[-1] == current_attempt
    assert await store.get(FactKey(kind="run", value=committed.id)) == updated


async def test_run_owned_event_and_fact_exact_replay_do_not_advance_versions() -> None:
    """Response loss replays the same child event and aggregate fact exactly."""
    store, committed, worker = await _persisted_committed_run()
    current_attempt = committed.attempts[-1]
    event = AttemptEvent(
        event_id="event-model-replay-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )
    authority = AttemptAuthority(
        current_fence=committed.current_fence,
        current_worker=worker.ref,
    )
    first = committed.record_current_attempt_event(
        attempt_id=current_attempt.id,
        event=event,
        authority=authority,
        worker=worker.ref,
        fence=committed.current_fence,
        expected_version=committed.version,
        expected_attempt_version=current_attempt.version,
    )
    await _persist(
        store,
        first,
        previous_version=committed.version,
        action="record-attempt-event-replay",
    )

    replay = first.record_current_attempt_event(
        attempt_id=current_attempt.id,
        event=event,
        authority=authority,
        worker=worker.ref,
        fence=committed.current_fence,
        expected_version=committed.version,
        expected_attempt_version=current_attempt.version,
    )
    await _persist(
        store,
        replay,
        previous_version=committed.version,
        action="record-attempt-event-replay",
    )

    assert replay is first
    assert replay.version == committed.version + 1
    assert replay.attempts[-1].version == current_attempt.version + 1
    assert replay.attempts[-1].events == (event,)
    assert await store.get(FactKey(kind="run", value=committed.id)) == first


@pytest.mark.parametrize("stale_layer", ["attempt", "run"])
async def test_run_owned_event_rejects_stale_cas_without_publishing(
    stale_layer: str,
) -> None:
    """Either stale CAS layer leaves the aggregate and committed fact unchanged."""
    store, committed, worker = await _persisted_committed_run()
    current_attempt = committed.attempts[-1]
    event = AttemptEvent(
        event_id=f"event-model-stale-{stale_layer}",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"stale_layer": stale_layer},
        ),
    )

    with pytest.raises(VersionConflict) as captured:
        committed.record_current_attempt_event(
            attempt_id=current_attempt.id,
            event=event,
            authority=AttemptAuthority(
                current_fence=committed.current_fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=committed.current_fence,
            expected_version=(
                committed.version - 1 if stale_layer == "run" else committed.version
            ),
            expected_attempt_version=(
                current_attempt.version + 1
                if stale_layer == "attempt"
                else current_attempt.version
            ),
        )

    assert captured.value.entity_type == stale_layer
    assert committed.attempts[-1] == current_attempt
    assert current_attempt.events == ()
    assert await store.get(FactKey(kind="run", value=committed.id)) == committed


async def test_run_owned_attempt_phase_is_persisted_as_one_monotonic_fact() -> None:
    """A current Attempt phase change advances the persisted Run aggregate."""
    store, committed, _ = await _persisted_committed_run()
    current_attempt = committed.attempts[-1]

    updated = committed.transition_current_attempt(
        attempt_id=current_attempt.id,
        target=AttemptState.PROVISIONING,
        expected_version=committed.version,
        expected_attempt_version=current_attempt.version,
    )
    await _persist(
        store,
        updated,
        previous_version=committed.version,
        action="attempt-provisioning",
    )

    assert updated.version == committed.version + 1
    assert updated.attempts[-1].version == current_attempt.version + 1
    assert updated.attempts[-1].state is AttemptState.PROVISIONING
    assert committed.attempts[-1].state is AttemptState.START_COMMITTED
    assert await store.get(FactKey(kind="run", value=committed.id)) == updated


async def test_run_owned_evidence_terminal_is_persisted_as_one_monotonic_fact() -> None:
    """Trusted Evidence finalizes the current Attempt inside the persisted Run."""
    store, uploading, worker, trusted_exit, candidate = await _persisted_uploading_run()
    uploading_attempt = uploading.attempts[-1]

    finalized = _finalize_current_evidence(uploading, worker, trusted_exit, candidate)
    await _persist(
        store,
        finalized.run,
        previous_version=uploading.version,
        action="finalize-attempt-evidence",
    )

    assert finalized.replayed is False
    assert finalized.run.version == uploading.version + 1
    assert finalized.attempt is finalized.run.attempts[-1]
    assert finalized.attempt.version == uploading_attempt.version + 1
    assert finalized.attempt.state is AttemptState.INFRA_FAILED
    assert finalized.evidence == candidate
    assert uploading.attempts[-1].state is AttemptState.UPLOADING
    assert await store.get(FactKey(kind="run", value=uploading.id)) == finalized.run


async def test_run_owned_evidence_and_fact_exact_replay_do_not_advance_versions() -> None:
    """A finalized normal Evidence response is replayed before both stale CAS checks."""
    store, uploading, worker, trusted_exit, candidate = await _persisted_uploading_run()
    uploading_attempt = uploading.attempts[-1]
    first = _finalize_current_evidence(uploading, worker, trusted_exit, candidate)
    await _persist(
        store,
        first.run,
        previous_version=uploading.version,
        action="finalize-attempt-evidence-replay",
    )

    replay = _finalize_current_evidence(
        first.run,
        worker,
        trusted_exit,
        candidate,
        expected_version=uploading.version,
        expected_attempt_version=uploading_attempt.version,
    )
    await _persist(
        store,
        replay.run,
        previous_version=uploading.version,
        action="finalize-attempt-evidence-replay",
    )

    assert replay.replayed is True
    assert replay.run is first.run
    assert replay.attempt is first.attempt
    assert replay.evidence is first.evidence
    assert replay.run.version == uploading.version + 1
    assert replay.attempt.version == uploading_attempt.version + 1
    assert await store.get(FactKey(kind="run", value=uploading.id)) == first.run


@pytest.mark.parametrize("stale_layer", ["attempt", "run"])
async def test_run_owned_evidence_rejects_stale_cas_without_publishing(
    stale_layer: str,
) -> None:
    """A stale Evidence CAS cannot publish a terminal child or aggregate snapshot."""
    store, uploading, worker, trusted_exit, candidate = await _persisted_uploading_run()
    uploading_attempt = uploading.attempts[-1]

    with pytest.raises(VersionConflict) as captured:
        _finalize_current_evidence(
            uploading,
            worker,
            trusted_exit,
            candidate,
            expected_version=(
                uploading.version - 1 if stale_layer == "run" else uploading.version
            ),
            expected_attempt_version=(
                uploading_attempt.version - 1
                if stale_layer == "attempt"
                else uploading_attempt.version
            ),
        )

    assert captured.value.entity_type == stale_layer
    assert uploading.attempts[-1] == uploading_attempt
    assert uploading_attempt.state is AttemptState.UPLOADING
    assert uploading_attempt.evidence is None
    assert await store.get(FactKey(kind="run", value=uploading.id)) == uploading


def _ready_worker() -> tuple[WorkerGeneration, WorkerAuthority]:
    worker = WorkerGeneration.register(
        ref=WorkerRef(worker_id="worker-model-001", generation=1),
        host_id="host-model-001",
        pool_id="pool-model",
        cert_serial="cert-model-001",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.worker-capabilities.v1",
            payload={"executor": "rootless-docker"},
        ),
        registered_at=REGISTERED_AT,
    )
    authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=worker.version,
        occurred_at=REGISTERED_AT + timedelta(seconds=1),
    )
    return ready, authority


async def _persisted_committed_run() -> tuple[InMemoryVersionedFactStore, Run, WorkerGeneration]:
    store = InMemoryVersionedFactStore()
    worker, worker_authority = _ready_worker()
    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-model-001", "profile_id": "profile-model"},
    )
    run = Run.create(run_id="run-model-001")
    await _persist(store, run, previous_version=None, action="create")
    queued = run.transition(RunState.QUEUED, expected_version=run.version)
    await _persist(store, queued, previous_version=run.version, action="queue")
    offered = queued.offer_assignment(
        assignment_id="assignment-model-001",
        worker=worker,
        worker_authority=worker_authority,
        spec_digest=spec_digest,
        offered_at=OFFERED_AT,
        expires_at=EXPIRES_AT,
        expected_version=queued.version,
    )
    await _persist(store, offered, previous_version=queued.version, action="offer")
    claimed = offered.claim_assignment(
        assignment_id="assignment-model-001",
        worker=worker.ref,
        observed_at=CLAIMED_AT,
        expected_version=offered.version,
    )
    await _persist(store, claimed, previous_version=offered.version, action="claim")
    committed = claimed.commit_start(
        assignment_id="assignment-model-001",
        worker=worker.ref,
        start_commit_key="commit-model-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-model-001",
        observed_at=COMMITTED_AT,
        expected_version=claimed.version,
    ).run
    await _persist(store, committed, previous_version=claimed.version, action="commit-start")
    return store, committed, worker


async def _persisted_uploading_run() -> tuple[
    InMemoryVersionedFactStore,
    Run,
    WorkerGeneration,
    TrustedExitFacts,
    EvidenceManifest,
]:
    store, committed, worker = await _persisted_committed_run()
    current_attempt = committed.attempts[-1]
    provisioning = committed.transition_current_attempt(
        attempt_id=current_attempt.id,
        target=AttemptState.PROVISIONING,
        expected_version=committed.version,
        expected_attempt_version=current_attempt.version,
    )
    await _persist(
        store,
        provisioning,
        previous_version=committed.version,
        action="attempt-provisioning",
    )
    uploading = provisioning.transition_current_attempt(
        attempt_id=current_attempt.id,
        target=AttemptState.UPLOADING,
        expected_version=provisioning.version,
        expected_attempt_version=provisioning.attempts[-1].version,
    )
    await _persist(
        store,
        uploading,
        previous_version=provisioning.version,
        action="attempt-uploading",
    )
    uploading_attempt = uploading.attempts[-1]
    trusted_exit = TrustedExitFacts(
        source_event_id="event-exit-model-001",
        pid=None,
        exit_class=PlatformExitClass.INFRA_FAILED,
        exit_code=None,
        signal=None,
        oom=False,
        timeout=False,
    )
    candidate = build_evidence_manifest(
        attempt_id=uploading_attempt.id,
        run_id=uploading_attempt.run_id,
        attempt_no=uploading_attempt.attempt_no,
        assignment_id=uploading_attempt.assignment_id,
        fence=uploading_attempt.fence,
        worker=uploading_attempt.worker,
        execution_spec_digest=uploading_attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=None,
        artifacts=(),
    )
    return store, uploading, worker, trusted_exit, candidate


def _finalize_current_evidence(
    run: Run,
    worker: WorkerGeneration,
    trusted_exit: TrustedExitFacts,
    candidate: EvidenceManifest,
    *,
    expected_version: int | None = None,
    expected_attempt_version: int | None = None,
) -> FinalizeAttemptEvidenceResult:
    attempt = run.attempts[-1]
    return run.finalize_current_attempt_evidence(
        attempt_id=attempt.id,
        proposal=EvidenceProposal(root_digest=candidate.root_digest),
        trusted_exit=trusted_exit,
        case_summary=None,
        artifacts=(),
        requirements=EvidenceRequirements(required_artifact_paths=frozenset()),
        authority=AttemptAuthority(
            current_fence=run.current_fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=run.current_fence,
        expected_version=run.version if expected_version is None else expected_version,
        expected_attempt_version=(
            attempt.version if expected_attempt_version is None else expected_attempt_version
        ),
    )


async def _persist(
    store: InMemoryVersionedFactStore,
    run: Run,
    *,
    previous_version: int | None,
    action: str,
) -> None:
    await store.commit(
        VersionedFactCommand(
            key=FactKey(kind="run", value=run.id),
            expected_version=previous_version,
            fact=run,
            idempotency=IdempotencyRecord.create(
                scope=f"model:run:{run.id}",
                key=action,
                request_digest=canonical_digest(
                    schema_version="qep.model-command.v1",
                    payload={
                        "action": action,
                        "run_id": run.id,
                        "run_version": run.version,
                    },
                ),
                response_status=200,
                response_ref=run.id,
            ),
        )
    )
