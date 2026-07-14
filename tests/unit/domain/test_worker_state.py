"""T-M0-STATE-001B/001C: WorkerGeneration state, authority, and shared CAS contracts."""

from datetime import UTC, datetime, timedelta

import pytest

REGISTERED_AT = datetime(2026, 7, 12, 12, tzinfo=UTC)
ASSIGNMENT_OFFERED_AT = REGISTERED_AT + timedelta(minutes=1)
ASSIGNMENT_EXPIRES_AT = ASSIGNMENT_OFFERED_AT + timedelta(hours=1)


def _registered_worker():
    from qarunner.domain import WorkerGeneration, WorkerRef, canonical_digest

    return WorkerGeneration.register(
        ref=WorkerRef(worker_id="worker-001", generation=3),
        host_id="host-001",
        pool_id="pool-default",
        cert_serial="cert-001",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.worker-capabilities.v1",
            payload={"executor": "docker", "platform": "linux/arm64"},
        ),
        registered_at=REGISTERED_AT,
    )


def _authority(worker, *, generation: int | None = None):
    from qarunner.domain import WorkerAuthority, WorkerRef

    return WorkerAuthority(
        current_ref=WorkerRef(
            worker_id=worker.ref.worker_id,
            generation=worker.ref.generation if generation is None else generation,
        )
    )


def _worker_in_state(state):
    from qarunner.domain import WorkerState

    worker = _registered_worker()
    if state is WorkerState.REGISTERING:
        return worker
    if state is WorkerState.READY:
        return worker.transition(
            WorkerState.READY,
            authority=_authority(worker),
            expected_version=worker.version,
            occurred_at=REGISTERED_AT + timedelta(seconds=1),
        )
    if state is WorkerState.BUSY:
        ready = _worker_in_state(WorkerState.READY)
        return ready.observe_assignments(
            authority=_authority(ready),
            active_assignments=1,
            expected_version=ready.version,
            observed_at=REGISTERED_AT + timedelta(seconds=2),
        )
    if state is WorkerState.DRAINING:
        ready = _worker_in_state(WorkerState.READY)
        return ready.transition(
            WorkerState.DRAINING,
            authority=_authority(ready),
            expected_version=ready.version,
            occurred_at=REGISTERED_AT + timedelta(seconds=2),
        )
    if state is WorkerState.OFFLINE:
        ready = _worker_in_state(WorkerState.READY)
        return ready.transition(
            WorkerState.OFFLINE,
            authority=_authority(ready),
            expected_version=ready.version,
            occurred_at=REGISTERED_AT + timedelta(seconds=2),
        )
    if state is WorkerState.QUARANTINED:
        return worker.transition(
            WorkerState.QUARANTINED,
            authority=_authority(worker),
            expected_version=worker.version,
            occurred_at=REGISTERED_AT + timedelta(seconds=1),
        )
    if state is WorkerState.RETIRED:
        quarantined = _worker_in_state(WorkerState.QUARANTINED)
        return quarantined.transition(
            WorkerState.RETIRED,
            authority=_authority(quarantined),
            expected_version=quarantined.version,
            occurred_at=REGISTERED_AT + timedelta(seconds=2),
        )
    raise AssertionError(f"unhandled test state: {state}")


def test_worker_state_registry_matches_m0_provisional_contract() -> None:
    from qarunner.domain import WORKER_TERMINAL_STATES, WORKER_TRANSITIONS, WorkerState

    assert set(WorkerState) == {
        WorkerState.REGISTERING,
        WorkerState.READY,
        WorkerState.BUSY,
        WorkerState.DRAINING,
        WorkerState.QUARANTINED,
        WorkerState.OFFLINE,
        WorkerState.RETIRED,
    }
    assert {
        WorkerState.REGISTERING: frozenset({WorkerState.READY, WorkerState.QUARANTINED}),
        WorkerState.READY: frozenset(
            {
                WorkerState.DRAINING,
                WorkerState.OFFLINE,
                WorkerState.QUARANTINED,
            }
        ),
        WorkerState.BUSY: frozenset(
            {
                WorkerState.DRAINING,
                WorkerState.OFFLINE,
                WorkerState.QUARANTINED,
            }
        ),
        WorkerState.DRAINING: frozenset(
            {WorkerState.RETIRED, WorkerState.OFFLINE, WorkerState.QUARANTINED}
        ),
        WorkerState.OFFLINE: frozenset({WorkerState.QUARANTINED}),
        WorkerState.QUARANTINED: frozenset({WorkerState.RETIRED}),
        WorkerState.RETIRED: frozenset(),
    } == WORKER_TRANSITIONS
    assert frozenset({WorkerState.RETIRED}) == WORKER_TERMINAL_STATES


@pytest.mark.parametrize("worker_id", ["", "   "])
def test_worker_ref_rejects_empty_identity(worker_id: str) -> None:
    from qarunner.domain import DomainValidationError, WorkerRef

    with pytest.raises(DomainValidationError) as caught:
        WorkerRef(worker_id=worker_id, generation=1)

    assert caught.value.code == "domain_validation_error"
    assert caught.value.field == "worker_id"
    assert caught.value.reason == "empty_id"


@pytest.mark.parametrize("generation", [0, -1])
def test_worker_ref_rejects_nonpositive_generation(generation: int) -> None:
    from qarunner.domain import DomainValidationError, WorkerRef

    with pytest.raises(DomainValidationError) as caught:
        WorkerRef(worker_id="worker-001", generation=generation)

    assert caught.value.field == "generation"
    assert caught.value.reason == "not_positive"


@pytest.mark.parametrize(
    ("worker_id", "generation", "field", "reason"),
    [
        pytest.param(123, 1, "worker_id", "not_string", id="worker-id-type"),
        pytest.param("worker-001", True, "generation", "not_integer", id="bool-generation"),
        pytest.param("worker-001", 1.5, "generation", "not_integer", id="float-generation"),
    ],
)
def test_worker_ref_rejects_wrong_runtime_types(
    worker_id: object, generation: object, field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError, WorkerRef

    with pytest.raises(DomainValidationError) as caught:
        WorkerRef(worker_id=worker_id, generation=generation)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_register_returns_an_immutable_registering_generation() -> None:
    from qarunner.domain import WorkerRef, WorkerState

    worker = _registered_worker()

    assert worker.ref == WorkerRef(worker_id="worker-001", generation=3)
    assert worker.state is WorkerState.REGISTERING
    assert worker.version == 0
    assert worker.registered_at == REGISTERED_AT
    assert worker.last_seen_at is None
    assert worker.retired_at is None


def test_registration_ready_edge_records_the_health_observation() -> None:
    from qarunner.domain import WorkerState

    registering = _registered_worker()
    observed_at = REGISTERED_AT + timedelta(seconds=1)

    ready = registering.transition(
        WorkerState.READY,
        authority=_authority(registering),
        expected_version=0,
        occurred_at=observed_at,
    )

    assert ready.last_seen_at == observed_at


def test_every_declared_worker_edge_returns_a_new_version() -> None:
    from qarunner.domain import WORKER_TRANSITIONS

    for source, targets in WORKER_TRANSITIONS.items():
        for target in targets:
            worker = _worker_in_state(source)
            occurred_at = REGISTERED_AT + timedelta(minutes=worker.version + 1)

            updated = worker.transition(
                target,
                authority=_authority(worker),
                expected_version=worker.version,
                occurred_at=occurred_at,
            )

            assert updated.state is target
            assert updated.version == worker.version + 1
            assert updated.state_changed_at == occurred_at
            assert worker.state is source
            assert worker.version == updated.version - 1


def test_every_undeclared_worker_edge_is_rejected_without_mutation() -> None:
    from qarunner.domain import WORKER_TRANSITIONS, InvalidTransition, WorkerState

    for source in WorkerState:
        worker = _worker_in_state(source)
        for target in set(WorkerState) - WORKER_TRANSITIONS[source]:
            with pytest.raises(InvalidTransition) as caught:
                worker.transition(
                    target,
                    authority=_authority(worker),
                    expected_version=worker.version,
                    occurred_at=REGISTERED_AT + timedelta(hours=1),
                )

            assert caught.value.entity_type == "worker_generation"
            assert caught.value.current_state is source
            assert caught.value.requested_state is target
            assert worker.state is source


def test_worker_transition_rejects_stale_cas_before_state_change() -> None:
    from qarunner.domain import VersionConflict, WorkerState

    worker = _registered_worker()

    with pytest.raises(VersionConflict) as caught:
        worker.transition(
            WorkerState.READY,
            authority=_authority(worker),
            expected_version=7,
            occurred_at=REGISTERED_AT + timedelta(seconds=1),
        )

    assert caught.value.current_version == 0
    assert caught.value.expected_version == 7
    assert worker.state is WorkerState.REGISTERING


def test_worker_transition_uses_shared_expected_version_runtime_validation() -> None:
    """Worker commands reject bool CAS input after authority succeeds."""
    from qarunner.domain import DomainValidationError, WorkerState

    worker = _registered_worker()

    with pytest.raises(DomainValidationError) as caught:
        worker.transition(
            WorkerState.READY,
            authority=_authority(worker),
            expected_version=False,  # type: ignore[arg-type]
            occurred_at=REGISTERED_AT + timedelta(seconds=1),
        )

    assert caught.value.entity_type == "worker_generation"
    assert caught.value.field == "expected_version"
    assert caught.value.reason == "not_integer"
    assert worker.state is WorkerState.REGISTERING
    assert worker.version == 0


@pytest.mark.parametrize(
    ("active_assignments", "drain_requested", "expected_state"),
    [
        pytest.param(0, False, "READY", id="idle"),
        pytest.param(2, False, "BUSY", id="active"),
        pytest.param(2, True, "DRAINING", id="drain"),
    ],
)
def test_offline_worker_recovers_only_through_authenticated_reconcile(
    active_assignments: int,
    drain_requested: bool,
    expected_state: str,
) -> None:
    from qarunner.domain import ReconcileWorkerFacts, WorkerState

    offline = _worker_in_state(WorkerState.OFFLINE)
    observed_at = REGISTERED_AT + timedelta(minutes=5)

    recovered = offline.reconcile(
        authority=_authority(offline),
        authenticated_ref=offline.ref,
        facts=ReconcileWorkerFacts(
            active_assignments=active_assignments,
            drain_requested=drain_requested,
        ),
        expected_version=offline.version,
        observed_at=observed_at,
    )

    assert recovered.state is WorkerState[expected_state]
    assert recovered.last_seen_at == observed_at
    assert recovered.state_changed_at == observed_at
    assert recovered.version == offline.version + 1
    assert offline.state is WorkerState.OFFLINE


def test_reconcile_rejects_a_noncurrent_generation() -> None:
    from qarunner.domain import ReconcileWorkerFacts, WorkerGenerationConflict, WorkerState

    offline = _worker_in_state(WorkerState.OFFLINE)

    with pytest.raises(WorkerGenerationConflict) as caught:
        offline.reconcile(
            authority=_authority(offline, generation=4),
            authenticated_ref=offline.ref,
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            expected_version=offline.version,
            observed_at=REGISTERED_AT + timedelta(minutes=5),
        )

    assert caught.value.code == "worker_generation_conflict"
    assert caught.value.reason == "generation_not_current"
    assert offline.state is WorkerState.OFFLINE


def test_reconcile_rejects_an_authenticated_identity_mismatch() -> None:
    from qarunner.domain import (
        ReconcileWorkerFacts,
        WorkerGenerationConflict,
        WorkerRef,
        WorkerState,
    )

    offline = _worker_in_state(WorkerState.OFFLINE)

    with pytest.raises(WorkerGenerationConflict):
        offline.reconcile(
            authority=_authority(offline),
            authenticated_ref=WorkerRef(worker_id="worker-001", generation=2),
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            expected_version=offline.version,
            observed_at=REGISTERED_AT + timedelta(minutes=5),
        )


def test_reconcile_rejects_a_retired_generation_even_when_identity_matches() -> None:
    from qarunner.domain import InvalidTransition, ReconcileWorkerFacts, WorkerState

    retired = _worker_in_state(WorkerState.RETIRED)

    with pytest.raises(InvalidTransition):
        retired.reconcile(
            authority=_authority(retired),
            authenticated_ref=retired.ref,
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            expected_version=retired.version,
            observed_at=retired.state_changed_at + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "field_value",
    [
        pytest.param(REGISTERED_AT.replace(tzinfo=None), id="naive"),
        pytest.param(datetime(2026, 7, 12, 7, tzinfo=UTC), id="before-registration"),
    ],
)
def test_worker_transition_rejects_invalid_utc_timeline(field_value: datetime) -> None:
    from qarunner.domain import DomainValidationError, WorkerState

    worker = _registered_worker()

    with pytest.raises(DomainValidationError) as caught:
        worker.transition(
            WorkerState.READY,
            authority=_authority(worker),
            expected_version=0,
            occurred_at=field_value,
        )

    assert caught.value.field == "occurred_at"


def test_retired_worker_records_retirement_and_is_absorbing() -> None:
    from qarunner.domain import InvalidTransition, WorkerState

    retired = _worker_in_state(WorkerState.RETIRED)

    assert retired.retired_at == retired.state_changed_at
    for target in WorkerState:
        with pytest.raises(InvalidTransition):
            retired.transition(
                target,
                authority=_authority(retired),
                expected_version=retired.version,
                occurred_at=retired.state_changed_at + timedelta(seconds=1),
            )


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"host_id": ""}, "host_id", "empty_value", id="host-id"),
        pytest.param({"pool_id": ""}, "pool_id", "empty_value", id="pool-id"),
        pytest.param({"cert_serial": ""}, "cert_serial", "empty_value", id="cert"),
        pytest.param({"agent_version": ""}, "agent_version", "empty_value", id="agent-version"),
        pytest.param({"state": "ready"}, "state", "unknown", id="state"),
        pytest.param({"version": -1}, "version", "negative", id="version"),
        pytest.param(
            {"state_changed_at": REGISTERED_AT - timedelta(seconds=1)},
            "state_changed_at",
            "before_registration",
            id="state-before-registration",
        ),
        pytest.param(
            {"last_seen_at": REGISTERED_AT - timedelta(seconds=1)},
            "last_seen_at",
            "before_registration",
            id="seen-before-registration",
        ),
        pytest.param(
            {"retired_at": REGISTERED_AT},
            "retired_at",
            "must_match_retired_state",
            id="retired-time-on-live-state",
        ),
        pytest.param(
            {"host_id": 123},
            "host_id",
            "not_string",
            id="host-id-type",
        ),
        pytest.param(
            {"capabilities_digest": "sha256:not-a-digest"},
            "capabilities_digest",
            "not_digest",
            id="capabilities-digest-type",
        ),
        pytest.param(
            {"registered_at": "2026-07-12T12:00:00Z"},
            "registered_at",
            "not_datetime",
            id="registered-at-type",
        ),
    ],
)
def test_worker_rehydration_rejects_inconsistent_facts(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_registered_worker(), **changes)

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_retired_time_must_equal_the_retirement_state_change() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError, WorkerState

    retired = _worker_in_state(WorkerState.RETIRED)

    with pytest.raises(DomainValidationError) as caught:
        replace(retired, retired_at=retired.registered_at)

    assert caught.value.field == "retired_at"
    assert caught.value.reason == "must_equal_state_changed_at"


def test_reconcile_observation_cannot_move_last_seen_backwards() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError, ReconcileWorkerFacts, WorkerState

    offline = _worker_in_state(WorkerState.OFFLINE)
    with_later_observation = replace(
        offline,
        last_seen_at=offline.state_changed_at + timedelta(minutes=2),
    )

    with pytest.raises(DomainValidationError) as caught:
        with_later_observation.reconcile(
            authority=_authority(with_later_observation),
            authenticated_ref=with_later_observation.ref,
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            expected_version=with_later_observation.version,
            observed_at=with_later_observation.state_changed_at + timedelta(minutes=1),
        )

    assert caught.value.reason == "before_last_seen"


def test_reconcile_rejects_a_worker_that_is_not_offline() -> None:
    from qarunner.domain import InvalidTransition, ReconcileWorkerFacts

    worker = _registered_worker()

    with pytest.raises(InvalidTransition):
        worker.reconcile(
            authority=_authority(worker),
            authenticated_ref=worker.ref,
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            expected_version=worker.version,
            observed_at=REGISTERED_AT + timedelta(hours=1),
        )


@pytest.mark.parametrize(
    ("active_assignments", "expected_state"),
    [pytest.param(1, "BUSY", id="becomes-busy"), pytest.param(0, "READY", id="becomes-ready")],
)
def test_ready_busy_state_is_derived_from_assignment_facts(
    active_assignments: int, expected_state: str
) -> None:
    from qarunner.domain import WorkerState

    worker = (
        _worker_in_state(WorkerState.READY)
        if active_assignments > 0
        else _worker_in_state(WorkerState.BUSY)
    )
    observed_at = REGISTERED_AT + timedelta(minutes=5)

    updated = worker.observe_assignments(
        authority=_authority(worker),
        active_assignments=active_assignments,
        expected_version=worker.version,
        observed_at=observed_at,
    )

    assert updated.state is WorkerState[expected_state]
    assert updated.last_seen_at == observed_at


def test_assignment_observation_cannot_bypass_registration() -> None:
    from qarunner.domain import InvalidTransition

    registering = _registered_worker()

    with pytest.raises(InvalidTransition):
        registering.observe_assignments(
            authority=_authority(registering),
            active_assignments=1,
            expected_version=0,
            observed_at=REGISTERED_AT + timedelta(seconds=1),
        )


def test_old_generation_cannot_self_authorize_a_state_change() -> None:
    from qarunner.domain import WorkerGenerationConflict, WorkerState

    stale_worker = _registered_worker()

    with pytest.raises(WorkerGenerationConflict) as caught:
        stale_worker.transition(
            WorkerState.READY,
            authority=_authority(stale_worker, generation=4),
            expected_version=0,
            occurred_at=REGISTERED_AT + timedelta(seconds=1),
        )

    assert caught.value.reason == "generation_not_current"
    assert stale_worker.state is WorkerState.REGISTERING


def test_run_offer_rejects_a_ready_but_superseded_generation() -> None:
    from qarunner.domain import (
        Run,
        RunState,
        WorkerGenerationConflict,
        WorkerState,
        canonical_digest,
    )

    worker = _worker_in_state(WorkerState.READY)
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)

    with pytest.raises(WorkerGenerationConflict):
        queued.offer_assignment(
            assignment_id="assignment-001",
            worker=worker,
            worker_authority=_authority(worker, generation=4),
            spec_digest=canonical_digest(
                schema_version="qep.execution-spec.v1",
                payload={"run_id": "run-001"},
            ),
            offered_at=ASSIGNMENT_OFFERED_AT,
            expires_at=ASSIGNMENT_EXPIRES_AT,
            expected_version=1,
        )

    assert queued.assignment is None


@pytest.mark.parametrize("state_name", ["READY", "BUSY"])
def test_run_offer_accepts_only_a_current_claimable_generation(state_name: str) -> None:
    from qarunner.domain import Run, RunState, WorkerState, canonical_digest

    worker = _worker_in_state(WorkerState[state_name])
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)

    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=worker,
        worker_authority=_authority(worker),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": "run-001"},
        ),
        offered_at=ASSIGNMENT_OFFERED_AT,
        expires_at=ASSIGNMENT_EXPIRES_AT,
        expected_version=1,
    )

    assert offered.state is RunState.ASSIGNED
    assert offered.assignment is not None
    assert offered.assignment.worker == worker.ref


@pytest.mark.parametrize(
    ("active_assignments", "drain_requested", "field", "reason"),
    [
        pytest.param(-1, False, "active_assignments", "negative", id="negative"),
        pytest.param(True, False, "active_assignments", "not_integer", id="bool-count"),
        pytest.param(0, "no", "drain_requested", "not_boolean", id="drain-type"),
    ],
)
def test_reconcile_facts_reject_invalid_values(
    active_assignments: object,
    drain_requested: object,
    field: str,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError, ReconcileWorkerFacts

    with pytest.raises(DomainValidationError) as caught:
        ReconcileWorkerFacts(
            active_assignments=active_assignments,  # type: ignore[arg-type]
            drain_requested=drain_requested,  # type: ignore[arg-type]
        )

    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "state_name", ["REGISTERING", "DRAINING", "OFFLINE", "QUARANTINED", "RETIRED"]
)
def test_run_offer_rejects_a_worker_that_cannot_claim(state_name: str) -> None:
    from qarunner.domain import Run, RunState, WorkerNotClaimable, WorkerState, canonical_digest

    worker = _worker_in_state(WorkerState[state_name])
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)

    with pytest.raises(WorkerNotClaimable) as caught:
        queued.offer_assignment(
            assignment_id="assignment-001",
            worker=worker,
            worker_authority=_authority(worker),
            spec_digest=canonical_digest(
                schema_version="qep.execution-spec.v1",
                payload={"run_id": "run-001"},
            ),
            offered_at=ASSIGNMENT_OFFERED_AT,
            expires_at=ASSIGNMENT_EXPIRES_AT,
            expected_version=1,
        )

    assert caught.value.reason == state_name.lower()
    assert queued.assignment is None
