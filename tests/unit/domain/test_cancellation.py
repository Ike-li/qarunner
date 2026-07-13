"""T-M0-CANCEL-001: cancellation intent is not an execution outcome."""

from datetime import UTC, datetime, timedelta, timezone


def _trusted_cancellation_stop(*, worker, intent_digest):
    from qarunner.domain import TrustedCancellationStop

    return TrustedCancellationStop(
        run_id="run-001",
        attempt_id="attempt-001",
        fence=1,
        worker=worker,
        cancellation_intent_digest=intent_digest,
        source_event_id="event-cancelled-001",
        process_stopped_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
        sut_access_stopped_at=datetime(2026, 7, 14, 0, 2, 30, tzinfo=UTC),
        recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )


def _cancel_requested_uploading_run():
    from dataclasses import replace

    from qarunner.domain import AttemptState, CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, worker = _committed_initial()
    provisioning = committed.attempt.transition(AttemptState.PROVISIONING, expected_version=0)
    running = provisioning.transition(AttemptState.RUNNING, expected_version=1)
    uploading = running.transition(AttemptState.UPLOADING, expected_version=2)
    uploading_run = replace(committed.run, attempts=(uploading,))
    requested = uploading_run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=uploading_run.version,
    )
    return requested, worker


def _cancel_finalize_inputs(*, run, stop):
    from qarunner.domain import (
        EvidenceProposal,
        EvidenceRequirements,
        PlatformExitClass,
        TrustedExitFacts,
        build_evidence_manifest,
    )

    attempt = run.attempts[-1]
    trusted_exit = TrustedExitFacts(
        source_event_id=stop.source_event_id,
        pid=731,
        exit_class=PlatformExitClass.CANCELLED,
        exit_code=None,
        signal=15,
        oom=False,
        timeout=False,
    )
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=None,
        artifacts=(),
        cancellation_stop=stop,
    )
    return (
        EvidenceProposal(root_digest=candidate.root_digest),
        trusted_exit,
        EvidenceRequirements(required_artifact_paths=frozenset()),
    )


def _passing_finalize_inputs(*, run):
    from qarunner.domain import (
        ArtifactClass,
        ArtifactPath,
        EvidenceProposal,
        EvidenceRequirements,
        PlatformExitClass,
        TrustedExitFacts,
        ValidatedCaseSummary,
        VerifiedArtifact,
        build_evidence_manifest,
        canonical_digest,
    )

    attempt = run.attempts[-1]
    path = ArtifactPath("case-results.json")
    artifact = VerifiedArtifact(
        path=path,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=16,
        digest=canonical_digest(
            schema_version="qep.test-cancel-result.v1",
            payload={"attempt_id": attempt.id},
        ),
    )
    case_summary = ValidatedCaseSummary(
        schema_version="qep.case-summary.v1",
        source_artifact_path=path,
        source_artifact_digest=artifact.digest,
        expected=1,
        passed=1,
        failed=0,
        skipped=0,
        not_reported=0,
        unexpected=0,
    )
    trusted_exit = TrustedExitFacts(
        source_event_id="event-completed-001",
        pid=731,
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=0,
        signal=None,
        oom=False,
        timeout=False,
    )
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=(artifact,),
    )
    return (
        EvidenceProposal(root_digest=candidate.root_digest),
        trusted_exit,
        case_summary,
        (artifact,),
        EvidenceRequirements(required_artifact_paths=frozenset({path})),
    )


def _finalized_cancelled_run():
    from qarunner.domain import AttemptAuthority

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]
    finalized = requested.finalize_cancelled_attempt_evidence(
        attempt_id=attempt.id,
        proposal=proposal,
        trusted_exit=trusted_exit,
        cancellation_stop=stop,
        case_summary=None,
        artifacts=(),
        requirements=requirements,
        authority=AttemptAuthority(
            current_fence=attempt.fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )
    return finalized, requested, worker, stop, proposal, trusted_exit, requirements


def _completed_after_cancel_run():
    from dataclasses import replace

    from qarunner.domain import AttemptAuthority

    requested, worker = _cancel_requested_uploading_run()
    attempt = requested.attempts[-1]
    proposal, trusted_exit, case_summary, artifacts, requirements = _passing_finalize_inputs(
        run=requested
    )
    completed = attempt.finalize_evidence(
        proposal=proposal,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=AttemptAuthority(
            current_fence=attempt.fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=attempt.version,
    )
    return replace(requested, attempts=(completed.attempt,)), completed, worker


def test_queued_run_cancel_records_intent_without_attempt_or_fence() -> None:
    from qarunner.domain import CancellationSource, Run, RunState

    queued = Run.create(run_id="run-001").transition(
        RunState.QUEUED,
        expected_version=0,
    )

    cancelled = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="superseded by a newer validation request",
        observed_at=datetime(2026, 7, 13, 6, tzinfo=UTC),
        expected_version=queued.version,
    )

    assert cancelled.state is RunState.CANCELLED
    assert cancelled.cancel_intent is not None
    assert cancelled.cancel_intent.run_id == cancelled.id
    assert cancelled.cancel_intent.idempotency_key == "cancel-001"
    assert cancelled.cancel_intent.source is CancellationSource.USER_REQUEST
    assert cancelled.assignments == ()
    assert cancelled.attempts == ()
    assert cancelled.current_fence == 0
    assert cancelled.version == queued.version + 1
    assert queued.state is RunState.QUEUED
    assert queued.cancel_intent is None


def test_planned_run_cancel_is_rejected_until_scheduling_intent_exists() -> None:
    import pytest

    from qarunner.domain import CancellationSource, InvalidTransition, Run

    planned = Run.create(run_id="run-001")

    with pytest.raises(InvalidTransition):
        planned.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="planning-stage cancellation is outside this contract",
            observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
            expected_version=planned.version,
        )

    assert planned.cancel_intent is None


def test_cancellation_intent_rejects_empty_run_id_stably() -> None:
    import pytest

    from qarunner.domain import (
        CancellationIntent,
        CancellationSource,
        DomainValidationError,
    )

    with pytest.raises(DomainValidationError) as caught:
        CancellationIntent(
            run_id="",
            idempotency_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="stop",
            recorded_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        )

    assert caught.value.entity_type == "cancellation_intent"
    assert caught.value.field == "run_id"
    assert caught.value.reason == "empty"


def test_cancellation_intent_rejects_invalid_values_stably() -> None:
    import pytest

    from qarunner.domain import (
        CancellationIntent,
        CancellationSource,
        DomainValidationError,
    )

    baseline = {
        "run_id": "run-001",
        "idempotency_key": "cancel-001",
        "source": CancellationSource.USER_REQUEST,
        "actor_id": "user-001",
        "reason": "stop",
        "recorded_at": datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
    }
    invalid_cases = (
        ({"run_id": 123}, "run_id", "not_string"),
        ({"idempotency_key": ""}, "idempotency_key", "empty"),
        ({"source": "user_request"}, "source", "unknown"),
        ({"actor_id": ""}, "actor_id", "empty"),
        ({"reason": ""}, "reason", "empty"),
        ({"recorded_at": "bad"}, "recorded_at", "not_datetime"),
        (
            {"recorded_at": datetime(2026, 7, 14, 0, 1)},
            "recorded_at",
            "not_utc",
        ),
        (
            {
                "recorded_at": datetime(
                    2026,
                    7,
                    14,
                    0,
                    1,
                    tzinfo=timezone(timedelta(hours=1)),
                )
            },
            "recorded_at",
            "not_utc",
        ),
    )

    for changes, field, reason in invalid_cases:
        with pytest.raises(DomainValidationError) as caught:
            CancellationIntent(**(baseline | changes))
        assert caught.value.entity_type == "cancellation_intent"
        assert caught.value.field == field
        assert caught.value.reason == reason


def test_cancellation_source_distinguishes_user_deadline_and_policy_intent() -> None:
    from qarunner.domain import CancellationSource

    assert tuple(source.value for source in CancellationSource) == (
        "user_request",
        "deadline_exceeded",
        "policy_enforcement",
    )


def test_cancel_persists_each_source_with_its_actor_and_reason() -> None:
    from qarunner.domain import CancellationSource, Run, RunState

    cases = (
        (CancellationSource.USER_REQUEST, "user-001", "user stopped the run"),
        (
            CancellationSource.DEADLINE_EXCEEDED,
            "deadline-controller",
            "run deadline elapsed",
        ),
        (
            CancellationSource.POLICY_ENFORCEMENT,
            "policy-engine",
            "target grant was revoked",
        ),
    )

    for index, (source, actor_id, reason) in enumerate(cases, start=1):
        queued = Run.create(run_id=f"run-00{index}").transition(
            RunState.QUEUED,
            expected_version=0,
        )
        cancelled = queued.request_cancel(
            cancel_key=f"cancel-00{index}",
            source=source,
            actor_id=actor_id,
            reason=reason,
            observed_at=datetime(2026, 7, 14, index, tzinfo=UTC),
            expected_version=queued.version,
        )

        assert cancelled.cancel_intent is not None
        assert cancelled.cancel_intent.source is source
        assert cancelled.cancel_intent.actor_id == actor_id
        assert cancelled.cancel_intent.reason == reason


def test_cancellation_intent_digests_separate_request_from_server_time() -> None:
    from dataclasses import replace

    from qarunner.domain import CancellationIntent, CancellationSource

    intent = CancellationIntent(
        run_id="run-001",
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        recorded_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
    )
    later_record = replace(
        intent,
        recorded_at=intent.recorded_at + timedelta(seconds=1),
    )
    caller_changes = (
        replace(intent, run_id="run-002"),
        replace(intent, idempotency_key="cancel-002"),
        replace(intent, source=CancellationSource.DEADLINE_EXCEEDED),
        replace(intent, actor_id="scheduler-001"),
        replace(intent, reason="deadline reached"),
    )

    assert later_record.request_digest == intent.request_digest
    assert later_record.digest != intent.digest
    assert all(changed.request_digest != intent.request_digest for changed in caller_changes)
    assert all(changed.digest != intent.digest for changed in caller_changes)


def test_run_rehydration_rejects_foreign_cancellation_intent_owner() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, Run, RunState

    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    cancelled = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=queued.version,
    )
    assert cancelled.cancel_intent is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(
            cancelled,
            cancel_intent=replace(cancelled.cancel_intent, run_id="run-foreign"),
        )

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "run_mismatch"


def test_run_rehydration_rejects_cancel_intent_on_nonconverging_state() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, Run, RunState

    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    cancelled = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=queued.version,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(cancelled, state=RunState.QUEUED)

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "not_allowed_for_state"


def test_run_rehydration_rejects_fake_cancellation_fact_types() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, Run, RunState

    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    cancelled = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=queued.version,
    )
    finalized, *_ = _finalized_cancelled_run()

    for run, changes, field in (
        (cancelled, {"cancel_intent": "bad"}, "cancel_intent"),
        (finalized.run, {"cancellation_stop": "bad"}, "cancellation_stop"),
    ):
        with pytest.raises(DomainValidationError) as caught:
            replace(run, **changes)
        assert caught.value.field == field
        assert caught.value.reason == "invalid_type"


def test_cancel_exact_replay_ignores_new_server_time_and_stale_cas() -> None:
    from qarunner.domain import CancellationSource, Run, RunState

    queued = Run.create(run_id="run-001").transition(
        RunState.QUEUED,
        expected_version=0,
    )
    recorded_at = datetime(2026, 7, 13, 6, tzinfo=UTC)
    first = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="superseded by a newer validation request",
        observed_at=recorded_at,
        expected_version=queued.version,
    )

    replay = first.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="superseded by a newer validation request",
        observed_at=recorded_at + timedelta(minutes=1),
        expected_version=0,
    )

    assert replay is first
    assert replay.cancel_intent is not None
    assert replay.cancel_intent.recorded_at == recorded_at
    assert replay.version == first.version


def test_cancel_key_reuse_with_changed_request_conflicts_before_cas() -> None:
    import pytest

    from qarunner.domain import CancellationSource, IdempotencyConflict, Run, RunState

    queued = Run.create(run_id="run-001").transition(
        RunState.QUEUED,
        expected_version=0,
    )
    first = queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="first reason",
        observed_at=datetime(2026, 7, 13, 6, tzinfo=UTC),
        expected_version=queued.version,
    )

    with pytest.raises(IdempotencyConflict) as caught:
        first.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="changed reason",
            observed_at=datetime(2026, 7, 13, 6, 1, tzinfo=UTC),
            expected_version=0,
        )

    assert caught.value.scope == "run:run-001:cancel"
    assert caught.value.key == "cancel-001"
    assert caught.value.stored_digest != caught.value.received_digest
    assert first.version == queued.version + 1


def test_second_cancel_key_cannot_replace_the_first_intent() -> None:
    import pytest

    from qarunner.domain import CancellationConflict, CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    first = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="first durable cancellation intent",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )

    with pytest.raises(CancellationConflict) as caught:
        first.request_cancel(
            cancel_key="cancel-002",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="replacement must not overwrite history",
            observed_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
            expected_version=first.version,
        )

    assert caught.value.run_id == first.id
    assert caught.value.stored_key == "cancel-001"
    assert caught.value.received_key == "cancel-002"
    assert first.cancel_intent is not None
    assert first.cancel_intent.idempotency_key == "cancel-001"
    assert first.version == committed.run.version + 1


def test_postcommit_cancel_records_intent_without_claiming_an_outcome() -> None:
    from qarunner.domain import AssignmentState, CancellationSource, RunState
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    run = committed.run

    requested = run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the long-running validation",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=run.version,
    )

    assert requested.state is RunState.RUNNING
    assert requested.cancel_intent is not None
    assert requested.assignment == run.assignment
    assert requested.assignment is not None
    assert requested.assignment.state is AssignmentState.COMMITTED
    assert requested.attempts == run.attempts
    assert requested.attempts[-1].state == run.attempts[-1].state
    assert requested.current_fence == run.current_fence == 1
    assert requested.version == run.version + 1
    assert run.cancel_intent is None


def test_postcommit_cancel_rejects_time_before_start_commit() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _committed_initial,
    )

    committed, _ = _committed_initial()

    with pytest.raises(DomainValidationError) as caught:
        committed.run.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="backdated cancellation",
            observed_at=BEFORE_EXPIRY - timedelta(seconds=1),
            expected_version=committed.run.version,
        )

    assert caught.value.field == "observed_at"
    assert caught.value.reason == "before_current_attempt_commit"
    assert committed.run.cancel_intent is None


def test_postcommit_cancel_preserves_exact_commit_replay() -> None:
    from qarunner.domain import CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import (
        _committed_initial,
        _digest,
    )

    committed, worker = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the long-running validation",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )

    replay = requested.commit_start(
        assignment_id="assignment-001",
        worker=worker.ref,
        start_commit_key="commit-001",
        spec_digest=_digest("execution-spec"),
        new_attempt_id="must-not-be-used",
        observed_at=datetime(2026, 7, 14, 1, tzinfo=UTC),
        expected_version=0,
    )

    assert replay.replayed is True
    assert replay.run is requested
    assert replay.attempt is requested.attempts[-1]
    assert replay.fence == 1


def test_postcommit_cancel_blocks_a_new_adjudicated_retry() -> None:
    import pytest

    from qarunner.domain import CancellationSource, RetryNotAllowed
    from tests.unit.domain.test_unknown_adjudicated_retry import (
        _adjudicated_run,
        _retry_intent,
    )

    adjudicated, _, _, adjudication = _adjudicated_run()
    requested = adjudicated.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="do not retry this validation",
        observed_at=datetime(2026, 7, 12, 22, tzinfo=UTC),
        expected_version=adjudicated.version,
    )
    intent = _retry_intent(adjudication=adjudication)

    with pytest.raises(RetryNotAllowed) as caught:
        requested.queue_adjudicated_retry(
            retry_intent=intent,
            expected_version=requested.version,
        )

    assert caught.value.reason == "cancel_requested"
    assert requested.state is adjudicated.state
    assert requested.retry_intents == ()
    assert requested.current_fence == adjudicated.current_fence


def test_offered_assignment_cancel_closes_prestart_without_attempt() -> None:
    from qarunner.domain import AssignmentState, CancellationSource, RunState
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()

    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="superseded before execution",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )

    assert cancelled.state is RunState.CANCELLED
    assert cancelled.assignment is None
    assert cancelled.current_assignment_id is None
    assert cancelled.assignments[-1].state is AssignmentState.CANCELLED_PRESTART
    assert cancelled.assignments[-1].closure is not None
    assert cancelled.cancel_intent is not None
    assert (
        cancelled.assignments[-1].closure.cancellation_intent_digest
        == cancelled.cancel_intent.digest
    )
    assert cancelled.attempts == ()
    assert cancelled.current_fence == 0
    assert cancelled.version == offered.version + 1


def test_prestart_cancel_rehydration_rejects_intent_closure_digest_mismatch() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="original intent",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    assert cancelled.cancel_intent is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(
            cancelled,
            cancel_intent=replace(cancelled.cancel_intent, reason="tampered intent"),
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "cancellation_intent_mismatch"


def test_prestart_cancel_rehydration_rejects_closure_key_and_time_tamper() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    historical = cancelled.assignments[-1]
    assert historical.closure is not None
    tampered_closures = (
        (
            replace(historical.closure, idempotency_key="cancel-tampered"),
            "assignments",
            "cancellation_intent_mismatch",
        ),
        (
            replace(
                historical.closure,
                effective_at=historical.closure.effective_at - timedelta(microseconds=1),
            ),
            "assignments",
            "cancellation_intent_mismatch",
        ),
        (
            replace(
                historical.closure,
                recorded_at=historical.closure.recorded_at + timedelta(microseconds=1),
            ),
            "cancel_intent",
            "before_latest_assignment_closure",
        ),
    )

    for tampered, field, reason in tampered_closures:
        with pytest.raises(DomainValidationError) as caught:
            replace(
                cancelled,
                assignments=(replace(historical, closure=tampered),),
            )
        assert caught.value.field == field
        assert caught.value.reason == reason


def test_prestart_cancel_rehydration_requires_terminal_history_and_closure() -> None:
    from dataclasses import fields, replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, RunState
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )

    with pytest.raises(DomainValidationError) as nonterminal:
        replace(
            cancelled,
            state=RunState.QUEUED,
            cancel_intent=None,
        )
    assert nonterminal.value.field == "assignments"
    assert nonterminal.value.reason == "cancelled_prestart_not_terminal"

    historical = cancelled.assignments[-1]
    without_closure = object.__new__(type(historical))
    for field in fields(historical):
        object.__setattr__(
            without_closure,
            field.name,
            None if field.name == "closure" else getattr(historical, field.name),
        )
    with pytest.raises(DomainValidationError) as missing_closure:
        replace(cancelled, assignments=(without_closure,))
    assert missing_closure.value.field == "assignments"
    assert missing_closure.value.reason == "cancellation_closure_missing"


def test_assignment_closure_rejects_invalid_cancellation_binding_values() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, canonical_digest
    from tests.unit.domain.test_assignment_precommit_closure import (
        AT_EXPIRY,
        BEFORE_EXPIRY,
        _claimed_initial,
        _offered_initial,
    )

    offered, worker = _offered_initial()
    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    cancel_closure = cancelled.assignments[-1].closure
    assert cancel_closure is not None
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    expiry_closure = expired.assignments[-1].closure
    assert expiry_closure is not None
    claimed, claimed_worker = _claimed_initial()
    released = claimed.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=claimed_worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )
    release_closure = released.assignments[-1].closure
    assert release_closure is not None
    other_digest = canonical_digest(
        schema_version="qep.cancellation-intent.v1",
        payload={"id": "other"},
    )
    invalid_cases = (
        (
            cancel_closure,
            {"cancellation_intent_digest": "bad"},
            "cancellation_intent_digest",
            "not_digest",
        ),
        (cancel_closure, {"worker": worker.ref}, "worker", "forbidden_for_kind"),
        (
            cancel_closure,
            {"cancellation_intent_digest": None},
            "cancellation_intent_digest",
            "required_for_kind",
        ),
        (
            expiry_closure,
            {"cancellation_intent_digest": other_digest},
            "cancellation_intent_digest",
            "forbidden_for_kind",
        ),
        (
            release_closure,
            {"cancellation_intent_digest": other_digest},
            "cancellation_intent_digest",
            "forbidden_for_kind",
        ),
    )

    for closure, changes, field, reason in invalid_cases:
        with pytest.raises(DomainValidationError) as caught:
            replace(closure, **changes)
        assert caught.value.entity_type == "assignment_closure"
        assert caught.value.field == field
        assert caught.value.reason == reason


def test_assignment_cancellation_closure_fact_not_request_binds_intent_digest() -> None:
    from dataclasses import replace

    from qarunner.domain import CancellationSource, canonical_digest
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    cancelled = offered.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    closure = cancelled.assignments[-1].closure
    assert closure is not None
    changed = replace(
        closure,
        cancellation_intent_digest=canonical_digest(
            schema_version="qep.cancellation-intent.v1",
            payload={"id": "changed"},
        ),
    )

    assert changed.request_digest == closure.request_digest
    assert changed.digest != closure.digest


def test_claimed_assignment_cancel_closes_prestart_without_attempt() -> None:
    from qarunner.domain import AssignmentClosureKind, AssignmentState, CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _claimed_initial,
    )

    claimed, worker = _claimed_initial()

    cancelled = claimed.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="superseded after reservation claim",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )

    historical = cancelled.assignments[-1]
    assert historical.state is AssignmentState.CANCELLED_PRESTART
    assert historical.worker == worker.ref
    assert historical.claimed_at == BEFORE_EXPIRY
    assert historical.closure is not None
    assert historical.closure.kind is AssignmentClosureKind.CANCELLED_PRESTART
    assert historical.closure.worker is None
    assert cancelled.assignment is None
    assert cancelled.attempts == ()
    assert cancelled.current_fence == 0


def test_cancel_rejects_corrupt_assigned_snapshot_without_current_pointer() -> None:
    import pytest

    from qarunner.domain import AssignmentConflict, CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _offered_initial,
    )
    from tests.unit.domain.test_unknown_adjudicated_retry import _unsafe_run_replace

    offered, _ = _offered_initial()
    corrupt = _unsafe_run_replace(offered, current_assignment_id=None)

    with pytest.raises(AssignmentConflict) as caught:
        corrupt.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="stop",
            observed_at=BEFORE_EXPIRY,
            expected_version=corrupt.version,
        )

    assert caught.value.reason == "current_assignment_missing"
    assert offered.assignment is not None


def test_cancel_at_assignment_expiry_preserves_expiry_as_closure_reason() -> None:
    from qarunner.domain import (
        AssignmentClosureKind,
        AssignmentState,
        CancellationSource,
        RunState,
    )
    from tests.unit.domain.test_assignment_precommit_closure import (
        AT_EXPIRY,
        _claimed_initial,
    )

    claimed, _ = _claimed_initial()

    cancelled = claimed.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel observed at the frozen expiry boundary",
        observed_at=AT_EXPIRY,
        expected_version=claimed.version,
    )

    historical = cancelled.assignments[-1]
    assert cancelled.state is RunState.CANCELLED
    assert cancelled.cancel_intent is not None
    assert historical.state is AssignmentState.EXPIRED_PRESTART
    assert historical.closure is not None
    assert historical.closure.kind is AssignmentClosureKind.EXPIRED_PRESTART
    assert historical.closure.effective_at == AT_EXPIRY
    assert historical.closure.recorded_at == AT_EXPIRY
    assert historical.closure.cancellation_intent_digest is None
    assert cancelled.assignment is None
    assert cancelled.attempts == ()
    assert cancelled.current_fence == 0


def test_cancel_after_expiry_does_not_rewrite_historical_closure() -> None:
    from qarunner.domain import AssignmentClosureKind, CancellationSource, RunState
    from tests.unit.domain.test_assignment_precommit_closure import (
        AT_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    historical = expired.assignments[-1]

    cancelled = expired.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel after expiry reconciliation",
        observed_at=AT_EXPIRY + timedelta(seconds=1),
        expected_version=expired.version,
    )

    assert cancelled.state is RunState.CANCELLED
    assert cancelled.assignments[-1] is historical
    assert cancelled.assignments[-1].closure is not None
    assert cancelled.assignments[-1].closure.kind is AssignmentClosureKind.EXPIRED_PRESTART
    assert cancelled.assignments[-1].closure.idempotency_key == "expiry-001"
    assert cancelled.cancel_intent is not None


def test_cancel_after_release_does_not_rewrite_historical_closure() -> None:
    from qarunner.domain import AssignmentClosureKind, CancellationSource, RunState
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _claimed_initial,
    )

    claimed, worker = _claimed_initial()
    released = claimed.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )
    historical = released.assignments[-1]

    cancelled = released.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel after worker release",
        observed_at=BEFORE_EXPIRY + timedelta(milliseconds=1),
        expected_version=released.version,
    )

    assert cancelled.state is RunState.CANCELLED
    assert cancelled.assignments[-1] is historical
    assert cancelled.assignments[-1].closure is not None
    assert cancelled.assignments[-1].closure.kind is AssignmentClosureKind.RELEASED_PRESTART
    assert cancelled.assignments[-1].closure.idempotency_key == "release-001"
    assert cancelled.cancel_intent is not None


def test_cancel_rejects_time_before_latest_prestart_closure() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        AT_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )

    with pytest.raises(DomainValidationError) as caught:
        expired.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="backdated behind the durable expiry closure",
            observed_at=AT_EXPIRY - timedelta(microseconds=1),
            expected_version=expired.version,
        )

    assert caught.value.field == "observed_at"
    assert caught.value.reason == "before_latest_assignment_closure"
    assert expired.cancel_intent is None


def test_cancel_rejects_time_before_latest_worker_release() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _claimed_initial,
    )

    claimed, worker = _claimed_initial()
    released = claimed.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )

    with pytest.raises(DomainValidationError) as caught:
        released.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="backdated behind the durable release closure",
            observed_at=BEFORE_EXPIRY - timedelta(microseconds=1),
            expected_version=released.version,
        )

    assert caught.value.field == "observed_at"
    assert caught.value.reason == "before_latest_assignment_closure"
    assert released.cancel_intent is None


def test_retry_queued_cancel_rejects_time_before_pending_retry_intent() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_unknown_adjudicated_retry import _queued_retry

    retry_queued, _, _, retry_intent = _queued_retry()

    with pytest.raises(DomainValidationError) as caught:
        retry_queued.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="backdated behind the durable retry authority",
            observed_at=retry_intent.created_at - timedelta(microseconds=1),
            expected_version=retry_queued.version,
        )

    assert caught.value.field == "observed_at"
    assert caught.value.reason == "before_pending_retry_intent"
    assert retry_queued.cancel_intent is None


def test_cancelled_run_rehydration_rejects_intent_before_latest_closure() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import (
        AT_EXPIRY,
        _offered_initial,
    )

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    cancelled = expired.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel after expiry reconciliation",
        observed_at=AT_EXPIRY + timedelta(seconds=1),
        expected_version=expired.version,
    )
    assert cancelled.cancel_intent is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(
            cancelled,
            cancel_intent=replace(
                cancelled.cancel_intent,
                recorded_at=AT_EXPIRY - timedelta(microseconds=1),
            ),
        )

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "before_latest_assignment_closure"


def test_cancelled_run_rehydration_rejects_intent_before_pending_retry() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_unknown_adjudicated_retry import _queued_retry

    retry_queued, _, _, retry_intent = _queued_retry()
    cancelled = retry_queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel after retry authorization",
        observed_at=retry_intent.created_at + timedelta(seconds=1),
        expected_version=retry_queued.version,
    )
    assert cancelled.cancel_intent is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(
            cancelled,
            cancel_intent=replace(
                cancelled.cancel_intent,
                recorded_at=retry_intent.created_at - timedelta(microseconds=1),
            ),
        )

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "before_pending_retry_intent"


def test_retry_queued_cancel_preserves_pending_intent_but_blocks_consumption() -> None:
    import pytest

    from qarunner.domain import AssignmentConflict, CancellationSource, RunState
    from tests.unit.domain.test_unknown_adjudicated_retry import (
        RETRY_OFFERED_AT,
        _queued_retry,
        _ready_worker,
    )

    retry_queued, _, spec_digest, retry_intent = _queued_retry()

    cancelled = retry_queued.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel before the approved retry is assigned",
        observed_at=RETRY_OFFERED_AT,
        expected_version=retry_queued.version,
    )

    assert cancelled.state is RunState.CANCELLED
    assert cancelled.attempts == retry_queued.attempts
    assert cancelled.retry_intents == (retry_intent,)
    assert cancelled.pending_retry_intent_id == retry_intent.id
    assert cancelled.pending_retry_intent is retry_intent
    assert cancelled.current_assignment_id is None
    assert cancelled.current_fence == retry_queued.current_fence

    worker, authority = _ready_worker(generation=4)
    with pytest.raises(AssignmentConflict) as caught:
        cancelled.offer_assignment(
            assignment_id="assignment-002",
            worker=worker,
            worker_authority=authority,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT + timedelta(minutes=1),
            expires_at=RETRY_OFFERED_AT + timedelta(hours=1),
            expected_version=cancelled.version,
        )

    assert caught.value.reason == "run_not_queued"


def test_retry_assignment_cancel_preserves_prior_attempt_and_retry_authority() -> None:
    from qarunner.domain import AssignmentState, CancellationSource, RunState
    from tests.unit.domain.test_unknown_adjudicated_retry import (
        RETRY_CLAIMED_AT,
        RETRY_EXPIRES_AT,
        RETRY_OFFERED_AT,
        _queued_retry,
        _ready_worker,
    )

    for claimed_stage in (False, True):
        retry_queued, _, spec_digest, retry_intent = _queued_retry()
        worker, authority = _ready_worker(generation=4)
        candidate = retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker,
            worker_authority=authority,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
        if claimed_stage:
            candidate = candidate.claim_assignment(
                assignment_id="assignment-002",
                worker=worker.ref,
                observed_at=RETRY_CLAIMED_AT,
                expected_version=candidate.version,
            )
        before_attempt = candidate.attempts[-1]

        cancelled = candidate.request_cancel(
            cancel_key=("cancel-claimed" if claimed_stage else "cancel-offered"),
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="do not start the approved retry",
            observed_at=RETRY_CLAIMED_AT + timedelta(minutes=1),
            expected_version=candidate.version,
        )

        assert cancelled.state is RunState.CANCELLED
        assert cancelled.assignments[-1].state is AssignmentState.CANCELLED_PRESTART
        assert cancelled.attempts[-1] is before_attempt
        assert cancelled.attempts == retry_queued.attempts
        assert cancelled.retry_intents == (retry_intent,)
        assert cancelled.pending_retry_intent is retry_intent
        assert cancelled.current_assignment_id is None
        assert cancelled.current_fence == retry_queued.current_fence == 1


def test_stop_proof_is_forbidden_without_a_converged_cancelled_attempt() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_unknown_adjudicated_retry import (
        RETRY_OFFERED_AT,
        _queued_retry,
    )

    retry_queued, _, _, _ = _queued_retry()
    retry_cancelled = retry_queued.request_cancel(
        cancel_key="cancel-retry-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="do not consume retry",
        observed_at=RETRY_OFFERED_AT,
        expected_version=retry_queued.version,
    )
    assert retry_cancelled.cancel_intent is not None
    retry_stop = _trusted_cancellation_stop(
        worker=retry_cancelled.attempts[-1].worker,
        intent_digest=retry_cancelled.cancel_intent.digest,
    )
    with pytest.raises(DomainValidationError) as pending_retry:
        replace(retry_cancelled, cancellation_stop=retry_stop)
    assert pending_retry.value.reason == "forbidden_without_cancelled_attempt"

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    running_stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    with pytest.raises(DomainValidationError) as running:
        replace(requested, cancellation_stop=running_stop)
    assert running.value.reason == "only_allowed_for_cancelled"


def test_cancel_winner_rejects_stale_commit_without_attempt_or_fence() -> None:
    import pytest

    from qarunner.domain import CancellationSource, VersionConflict
    from tests.unit.domain.test_assignment_precommit_closure import (
        BEFORE_EXPIRY,
        _claimed_initial,
        _digest,
    )

    claimed, worker = _claimed_initial()
    cancelled = claimed.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="cancel wins the aggregate CAS",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )

    with pytest.raises(VersionConflict):
        cancelled.commit_start(
            assignment_id="assignment-001",
            worker=worker.ref,
            start_commit_key="commit-001",
            spec_digest=_digest("execution-spec"),
            new_attempt_id="attempt-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=claimed.version,
        )

    assert cancelled.attempts == ()
    assert cancelled.current_fence == 0
    assert cancelled.cancel_intent is not None


def test_commit_winner_rejects_stale_cancel_without_recording_intent() -> None:
    import pytest

    from qarunner.domain import CancellationSource, VersionConflict
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    run = committed.run

    with pytest.raises(VersionConflict):
        run.request_cancel(
            cancel_key="cancel-001",
            source=CancellationSource.USER_REQUEST,
            actor_id="user-001",
            reason="stale cancellation loses the aggregate CAS",
            observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
            expected_version=run.version - 1,
        )

    assert run.cancel_intent is None
    assert run.attempts == (committed.attempt,)
    assert run.current_fence == committed.fence == 1


def test_cancel_stop_unproven_uses_intent_bound_unknown_facts() -> None:
    from qarunner.domain import (
        AttemptState,
        CancellationSource,
        RunState,
        UnknownReason,
        UnknownSource,
    )
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    attempt = requested.attempts[-1]

    unknown = requested.mark_cancel_stop_unproven(
        attempt_id=attempt.id,
        observation_id="unknown-cancel-001",
        recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )

    observation = unknown.attempts[-1].unknown_observation
    assert unknown.state is RunState.RUNNING
    assert unknown.cancel_intent is requested.cancel_intent
    assert unknown.attempts[-1].state is AttemptState.ATTEMPT_UNKNOWN
    assert observation is not None
    assert observation.reason is UnknownReason.CANCEL_STOP_UNPROVEN
    assert observation.source is UnknownSource.CANCEL_CONVERGENCE
    assert observation.review_basis_digest == requested.cancel_intent.digest


def test_cancel_stop_unproven_requires_existing_cancel_intent() -> None:
    import pytest

    from qarunner.domain import DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    attempt = committed.run.attempts[-1]

    with pytest.raises(DomainValidationError) as caught:
        committed.run.mark_cancel_stop_unproven(
            attempt_id=attempt.id,
            observation_id="unknown-cancel-001",
            recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
            expected_version=committed.run.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "required_for_cancel_convergence"


def test_cancel_stop_unproven_exact_replay_wins_before_both_cas_checks() -> None:
    from qarunner.domain import CancellationSource
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    attempt = requested.attempts[-1]
    recorded_at = datetime(2026, 7, 14, 0, 3, tzinfo=UTC)
    first = requested.mark_cancel_stop_unproven(
        attempt_id=attempt.id,
        observation_id="unknown-cancel-001",
        recorded_at=recorded_at,
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )

    replay = first.mark_cancel_stop_unproven(
        attempt_id=attempt.id,
        observation_id="unknown-cancel-001",
        recorded_at=recorded_at,
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )

    assert replay is first
    assert replay.version == first.version
    assert replay.attempts[-1].version == first.attempts[-1].version


def test_cancel_stop_unproven_rejects_observation_before_cancel_intent() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(DomainValidationError) as caught:
        requested.mark_cancel_stop_unproven(
            attempt_id=attempt.id,
            observation_id="unknown-cancel-001",
            recorded_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            expected_version=requested.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "recorded_at"
    assert caught.value.reason == "before_cancel_intent"
    assert requested.attempts[-1] is attempt


def test_cancel_stop_unproven_rejects_non_utc_observation_time_stably() -> None:
    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(DomainValidationError) as caught:
        requested.mark_cancel_stop_unproven(
            attempt_id=attempt.id,
            observation_id="unknown-cancel-001",
            recorded_at=datetime(2026, 7, 14, 0, 3),
            expected_version=requested.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.field == "recorded_at"
    assert caught.value.reason == "not_utc"


def test_trusted_cancellation_stop_digest_binds_execution_and_access_stop() -> None:
    from dataclasses import replace

    from qarunner.domain import CancellationSource, TrustedCancellationStop
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, worker = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    assert requested.cancel_intent is not None

    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    changed_access_fact = replace(
        stop,
        sut_access_stopped_at=stop.sut_access_stopped_at + timedelta(seconds=1),
    )

    assert isinstance(stop, TrustedCancellationStop)
    assert stop.run_id == requested.id
    assert stop.attempt_id == committed.attempt.id
    assert stop.fence == committed.fence
    assert stop.worker == worker.ref
    assert stop.digest != changed_access_fact.digest


def test_trusted_cancellation_stop_rejects_invalid_proof_values_stably() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, worker = _committed_initial()
    requested = committed.run.request_cancel(
        cancel_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop the committed attempt",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=committed.run.version,
    )
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    invalid_cases = (
        ({"fence": True}, "fence", "not_integer"),
        ({"fence": 0}, "fence", "not_positive"),
        ({"worker": "bad"}, "worker", "invalid_type"),
        (
            {"cancellation_intent_digest": "bad"},
            "cancellation_intent_digest",
            "not_digest",
        ),
        ({"source_event_id": ""}, "source_event_id", "empty"),
        (
            {"process_stopped_at": "bad"},
            "process_stopped_at",
            "not_datetime",
        ),
        (
            {"sut_access_stopped_at": datetime(2026, 7, 14, 0, 2)},
            "sut_access_stopped_at",
            "not_utc",
        ),
        (
            {
                "recorded_at": datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
                "sut_access_stopped_at": datetime(2026, 7, 14, 0, 2, 30, tzinfo=UTC),
            },
            "recorded_at",
            "before_stop_facts",
        ),
    )

    for changes, field, reason in invalid_cases:
        with pytest.raises(DomainValidationError) as caught:
            replace(stop, **changes)
        assert caught.value.entity_type == "trusted_cancellation_stop"
        assert caught.value.field == field
        assert caught.value.reason == reason


def test_generic_attempt_finalize_cannot_bypass_run_owned_cancellation() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import (
        AttemptAuthority,
        AttemptState,
        EvidenceNotReady,
    )
    from tests.unit.domain.test_assignment_precommit_closure import (
        _committed_initial,
        _digest,
    )

    committed, worker = _committed_initial()
    provisioning = committed.attempt.transition(
        AttemptState.PROVISIONING,
        expected_version=committed.attempt.version,
    )
    uploading = provisioning.transition(
        AttemptState.UPLOADING,
        expected_version=provisioning.version,
    )
    uploading_run = replace(committed.run, attempts=(uploading,))
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=_digest("not-a-run-cancellation-intent"),
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=uploading_run,
        stop=stop,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        uploading.finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=uploading.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=uploading.fence,
            expected_version=uploading.version,
            cancellation_stop=stop,
        )

    assert caught.value.reason == "cancelled evidence requires Run-owned finalization"
    assert uploading.state is AttemptState.UPLOADING
    assert uploading.evidence is None


def test_running_run_rehydration_rejects_unconverged_cancelled_attempt() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import (
        AttemptState,
        DomainValidationError,
        build_evidence_manifest,
    )
    from tests.unit.domain.test_assignment_precommit_closure import (
        _committed_initial,
        _digest,
    )

    committed, worker = _committed_initial()
    provisioning = committed.attempt.transition(
        AttemptState.PROVISIONING,
        expected_version=committed.attempt.version,
    )
    uploading = provisioning.transition(
        AttemptState.UPLOADING,
        expected_version=provisioning.version,
    )
    uploading_run = replace(committed.run, attempts=(uploading,))
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=_digest("not-a-run-cancellation-intent"),
    )
    _, trusted_exit, _ = _cancel_finalize_inputs(run=uploading_run, stop=stop)
    manifest = build_evidence_manifest(
        attempt_id=uploading.id,
        run_id=uploading.run_id,
        attempt_no=uploading.attempt_no,
        assignment_id=uploading.assignment_id,
        fence=uploading.fence,
        worker=uploading.worker,
        execution_spec_digest=uploading.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=None,
        artifacts=(),
        cancellation_stop=stop,
    )
    cancelled_attempt = replace(
        uploading,
        state=AttemptState.CANCELLED,
        evidence=manifest,
        version=uploading.version + 1,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(uploading_run, attempts=(cancelled_attempt,))

    assert caught.value.field == "attempts"
    assert caught.value.reason == "cancelled_attempt_requires_converged_run"
    assert uploading_run.attempts[-1] is uploading


def test_run_owned_cancel_finalize_converges_attempt_and_run_atomically() -> None:
    from qarunner.domain import AttemptAuthority, AttemptState, RunState

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]

    finalized = requested.finalize_cancelled_attempt_evidence(
        attempt_id=attempt.id,
        proposal=proposal,
        trusted_exit=trusted_exit,
        cancellation_stop=stop,
        case_summary=None,
        artifacts=(),
        requirements=requirements,
        authority=AttemptAuthority(
            current_fence=attempt.fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )

    assert finalized.replayed is False
    assert finalized.run.state is RunState.CANCELLED
    assert finalized.run.current_assignment_id is None
    assert finalized.run.assignment is None
    assert finalized.run.assignments == requested.assignments
    assert finalized.run.current_fence == requested.current_fence
    assert finalized.run.cancellation_stop is stop
    assert finalized.attempt is finalized.run.attempts[-1]
    assert finalized.attempt.state is AttemptState.CANCELLED
    assert finalized.attempt.evidence == finalized.evidence
    assert finalized.evidence.cancellation_stop is stop
    assert finalized.run.version == requested.version + 1
    assert finalized.attempt.version == attempt.version + 1


def test_run_owned_cancel_finalize_exact_replay_wins_before_both_cas_checks() -> None:
    from qarunner.domain import AttemptAuthority

    first, requested, worker, stop, proposal, trusted_exit, requirements = (
        _finalized_cancelled_run()
    )
    current = first.run.attempts[-1]

    replay = first.run.finalize_cancelled_attempt_evidence(
        attempt_id=current.id,
        proposal=proposal,
        trusted_exit=trusted_exit,
        cancellation_stop=stop,
        case_summary=None,
        artifacts=(),
        requirements=requirements,
        authority=AttemptAuthority(
            current_fence=current.fence,
            current_worker=worker.ref,
        ),
        worker=worker.ref,
        fence=current.fence,
        expected_version=requested.version,
        expected_attempt_version=requested.attempts[-1].version,
    )

    assert replay.replayed is True
    assert replay.run is first.run
    assert replay.attempt is current
    assert replay.evidence is first.evidence


def test_finalized_cancel_rejects_late_event_but_replays_existing_event() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import (
        AttemptAuthority,
        AttemptEvent,
        AttemptEventRejected,
        InvalidTransition,
        RunState,
        canonical_digest,
    )

    requested, worker = _cancel_requested_uploading_run()
    attempt = requested.attempts[-1]
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=worker.ref,
    )
    existing = AttemptEvent(
        event_id="event-progress-001",
        event_seq=1,
        event_type="progress",
        payload_digest=canonical_digest(
            schema_version="qep.test-cancel-event.v1",
            payload={"progress": 75},
        ),
    )
    with_event = attempt.record_event(
        existing,
        authority=authority,
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=attempt.version,
    )
    requested = replace(requested, attempts=(with_event,))
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    finalized = requested.finalize_cancelled_attempt_evidence(
        attempt_id=with_event.id,
        proposal=proposal,
        trusted_exit=trusted_exit,
        cancellation_stop=stop,
        case_summary=None,
        artifacts=(),
        requirements=requirements,
        authority=authority,
        worker=worker.ref,
        fence=with_event.fence,
        expected_version=requested.version,
        expected_attempt_version=with_event.version,
    )

    replay = finalized.attempt.record_event(
        existing,
        authority=authority,
        worker=worker.ref,
        fence=with_event.fence,
        expected_version=with_event.version,
    )
    late = AttemptEvent(
        event_id="event-late-002",
        event_seq=2,
        event_type="late_progress",
        payload_digest=canonical_digest(
            schema_version="qep.test-cancel-event.v1",
            payload={"progress": 100},
        ),
    )
    with pytest.raises(AttemptEventRejected) as event_error:
        finalized.attempt.record_event(
            late,
            authority=authority,
            worker=worker.ref,
            fence=with_event.fence,
            expected_version=finalized.attempt.version,
        )
    with pytest.raises(InvalidTransition):
        finalized.run.transition(
            RunState.RUNNING,
            expected_version=finalized.run.version,
        )

    assert replay is finalized.attempt
    assert event_error.value.reason == "attempt_terminal"
    assert finalized.run.attempts[-1].events == (existing,)
    assert finalized.run.attempts[-1].evidence is finalized.evidence
    assert finalized.run.state is RunState.CANCELLED


def test_cancelled_evidence_winner_rejects_late_completed_evidence() -> None:
    import pytest

    from qarunner.domain import AttemptAuthority, EvidenceConflict

    finalized, _, worker, *_ = _finalized_cancelled_run()
    current = finalized.attempt
    proposal, trusted_exit, case_summary, artifacts, requirements = _passing_finalize_inputs(
        run=finalized.run
    )

    with pytest.raises(EvidenceConflict):
        current.finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=current.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=current.fence,
            expected_version=current.version,
        )

    assert finalized.run.attempts[-1] is current
    assert finalized.run.attempts[-1].evidence is finalized.evidence


def test_completed_evidence_winner_rejects_late_cancelled_evidence() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptAuthority, AttemptState, EvidenceConflict, RunState

    requested, worker = _cancel_requested_uploading_run()
    attempt = requested.attempts[-1]
    proposal, trusted_exit, case_summary, artifacts, requirements = _passing_finalize_inputs(
        run=requested
    )
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=worker.ref,
    )
    completed = attempt.finalize_evidence(
        proposal=proposal,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=authority,
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=attempt.version,
    )
    completed_run = replace(requested, attempts=(completed.attempt,))
    assert completed_run.cancel_intent is requested.cancel_intent
    assert completed_run.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=completed_run.cancel_intent.digest,
    )
    cancel_proposal, cancelled_exit, cancel_requirements = _cancel_finalize_inputs(
        run=completed_run,
        stop=stop,
    )

    with pytest.raises(EvidenceConflict):
        completed_run.finalize_cancelled_attempt_evidence(
            attempt_id=completed.attempt.id,
            proposal=cancel_proposal,
            trusted_exit=cancelled_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=cancel_requirements,
            authority=authority,
            worker=worker.ref,
            fence=completed.attempt.fence,
            expected_version=completed_run.version,
            expected_attempt_version=completed.attempt.version,
        )

    assert completed_run.state is RunState.RUNNING
    assert completed_run.attempts[-1].state is AttemptState.PASSED
    assert completed_run.attempts[-1].evidence is completed.evidence
    assert completed_run.cancellation_stop is None


def test_attempt_rehydration_rejects_evidence_state_mismatch() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptState, DomainValidationError

    _, completed, _ = _completed_after_cancel_run()

    with pytest.raises(DomainValidationError) as caught:
        replace(completed.attempt, state=AttemptState.TEST_FAILED)

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "evidence"
    assert caught.value.reason == "outcome_mismatch"


def test_attempt_rehydration_rejects_invalid_evidence_outcome() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    finalized, *_ = _finalized_cancelled_run()
    invalid_evidence = replace(finalized.evidence, outcome="bad")

    with pytest.raises(DomainValidationError) as caught:
        replace(finalized.attempt, evidence=invalid_evidence)

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "evidence"
    assert caught.value.reason == "outcome_invalid"


def test_run_rehydration_defends_against_unsafe_attempt_evidence_state_mismatch() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptState, DomainValidationError
    from tests.unit.domain.test_unknown_retry_guard import _unsafe_attempt_replace

    completed_run, completed, _ = _completed_after_cancel_run()
    unsafe = _unsafe_attempt_replace(
        completed.attempt,
        state=AttemptState.TEST_FAILED,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(completed_run, attempts=(unsafe,))

    assert caught.value.field == "attempts"
    assert caught.value.reason == "evidence_state_mismatch"


def test_running_run_rehydration_rejects_malformed_evidence_snapshot() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    completed_run, completed, _ = _completed_after_cancel_run()
    malformed_evidence = replace(completed.evidence, artifacts=("bad",))
    malformed_attempt = replace(completed.attempt, evidence=malformed_evidence)

    with pytest.raises(DomainValidationError) as caught:
        replace(completed_run, attempts=(malformed_attempt,))

    assert caught.value.field == "attempts"
    assert caught.value.reason == "evidence_root_mismatch"


def test_run_owned_cancel_finalize_conflicting_stop_proof_is_rejected() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptAuthority, EvidenceConflict

    first, _, worker, stop, *_ = _finalized_cancelled_run()
    changed_stop = replace(
        stop,
        sut_access_stopped_at=stop.sut_access_stopped_at + timedelta(seconds=1),
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=first.run,
        stop=changed_stop,
    )
    current = first.run.attempts[-1]

    with pytest.raises(EvidenceConflict):
        first.run.finalize_cancelled_attempt_evidence(
            attempt_id=current.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=changed_stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=current.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=current.fence,
            expected_version=first.run.version,
            expected_attempt_version=current.version,
        )

    assert first.run.cancellation_stop is stop
    assert first.run.attempts[-1].evidence is first.evidence


def test_run_owned_cancel_finalize_rejects_retired_worker_generation() -> None:
    import pytest

    from qarunner.domain import AttemptAuthority, StaleGeneration, WorkerRef

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(StaleGeneration):
        requested.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=attempt.fence,
                current_worker=WorkerRef(
                    worker_id=worker.ref.worker_id,
                    generation=worker.ref.generation + 1,
                ),
            ),
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=requested.version,
            expected_attempt_version=attempt.version,
        )

    assert requested.state.value == "running"
    assert requested.attempts[-1].evidence is None


def test_run_owned_cancel_finalize_requires_fresh_run_cas() -> None:
    import pytest

    from qarunner.domain import AttemptAuthority, VersionConflict

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(VersionConflict):
        requested.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=attempt.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=requested.version - 1,
            expected_attempt_version=attempt.version,
        )

    assert requested.state.value == "running"
    assert requested.cancellation_stop is None
    assert requested.attempts[-1] is attempt


def test_run_owned_cancel_finalize_requires_fresh_attempt_cas() -> None:
    import pytest

    from qarunner.domain import AttemptAuthority, VersionConflict

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(VersionConflict):
        requested.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=attempt.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=requested.version,
            expected_attempt_version=attempt.version - 1,
        )

    assert requested.state.value == "running"
    assert requested.cancellation_stop is None
    assert requested.attempts[-1] is attempt


def test_run_owned_cancel_finalize_rejects_stop_fact_before_intent() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import EvidenceNotReady

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    backdated_stop = replace(
        stop,
        process_stopped_at=requested.cancel_intent.recorded_at - timedelta(microseconds=1),
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=backdated_stop,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(EvidenceNotReady) as caught:
        requested.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=backdated_stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=None,
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=requested.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.reason == "cancellation stop proof predates the intent"
    assert requested.cancellation_stop is None
    assert requested.attempts[-1] is attempt


def test_run_owned_cancel_finalize_rejects_stop_for_another_intent() -> None:
    import pytest

    from qarunner.domain import EvidenceNotReady, canonical_digest

    requested, worker = _cancel_requested_uploading_run()
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=canonical_digest(
            schema_version="qep.cancellation-intent.v1",
            payload={"run_id": requested.id, "key": "different"},
        ),
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    attempt = requested.attempts[-1]

    with pytest.raises(EvidenceNotReady) as caught:
        requested.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=None,  # rejected before authority is consulted
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=requested.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.reason == "cancellation stop proof does not match intent"
    assert requested.attempts[-1].evidence is None


def test_run_owned_cancel_finalize_requires_a_run_cancellation_intent() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import EvidenceNotReady

    requested, worker = _cancel_requested_uploading_run()
    assert requested.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=requested.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=requested,
        stop=stop,
    )
    without_intent = replace(requested, cancel_intent=None)
    attempt = without_intent.attempts[-1]

    with pytest.raises(EvidenceNotReady) as caught:
        without_intent.finalize_cancelled_attempt_evidence(
            attempt_id=attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=None,
            worker=worker.ref,
            fence=attempt.fence,
            expected_version=without_intent.version,
            expected_attempt_version=attempt.version,
        )

    assert caught.value.reason == "cancellation intent is missing"


def test_late_cancel_stop_proof_cannot_rewrite_unknown_attempt() -> None:
    import pytest

    from qarunner.domain import AttemptAuthority, AttemptState, InvalidTransition

    requested, worker = _cancel_requested_uploading_run()
    attempt = requested.attempts[-1]
    unknown = requested.mark_cancel_stop_unproven(
        attempt_id=attempt.id,
        observation_id="unknown-cancel-001",
        recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
        expected_version=requested.version,
        expected_attempt_version=attempt.version,
    )
    assert unknown.cancel_intent is not None
    stop = _trusted_cancellation_stop(
        worker=worker.ref,
        intent_digest=unknown.cancel_intent.digest,
    )
    proposal, trusted_exit, requirements = _cancel_finalize_inputs(
        run=unknown,
        stop=stop,
    )
    unknown_attempt = unknown.attempts[-1]

    with pytest.raises(InvalidTransition):
        unknown.finalize_cancelled_attempt_evidence(
            attempt_id=unknown_attempt.id,
            proposal=proposal,
            trusted_exit=trusted_exit,
            cancellation_stop=stop,
            case_summary=None,
            artifacts=(),
            requirements=requirements,
            authority=AttemptAuthority(
                current_fence=unknown_attempt.fence,
                current_worker=worker.ref,
            ),
            worker=worker.ref,
            fence=unknown_attempt.fence,
            expected_version=unknown.version,
            expected_attempt_version=unknown_attempt.version,
        )

    assert unknown.attempts[-1].state is AttemptState.ATTEMPT_UNKNOWN
    assert unknown.attempts[-1].evidence is None
    assert unknown.cancellation_stop is None


def test_cancelled_run_rehydration_rejects_foreign_stop_proof_owner() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    finalized, _, _, stop, *_ = _finalized_cancelled_run()
    foreign_stop = replace(stop, run_id="run-foreign")
    foreign_evidence = replace(
        finalized.evidence,
        cancellation_stop=foreign_stop,
    )
    foreign_attempt = replace(
        finalized.attempt,
        evidence=foreign_evidence,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            finalized.run,
            attempts=(foreign_attempt,),
            cancellation_stop=foreign_stop,
        )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "cancellation_stop"
    assert caught.value.reason == "binding_mismatch"


def test_cancelled_run_rehydration_rejects_coherent_backdated_intent() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError, build_evidence_manifest

    finalized, _, _, stop, *_ = _finalized_cancelled_run()
    committed_at = finalized.run.assignments[-1].committed_at
    assert committed_at is not None
    assert finalized.run.cancel_intent is not None
    early_intent = replace(
        finalized.run.cancel_intent,
        recorded_at=committed_at - timedelta(microseconds=1),
    )
    early_stop = replace(
        stop,
        cancellation_intent_digest=early_intent.digest,
    )
    current = finalized.attempt
    early_evidence = build_evidence_manifest(
        attempt_id=current.id,
        run_id=current.run_id,
        attempt_no=current.attempt_no,
        assignment_id=current.assignment_id,
        fence=current.fence,
        worker=current.worker,
        execution_spec_digest=current.spec_digest,
        trusted_exit=finalized.evidence.platform_exit,
        case_summary=finalized.evidence.case_summary,
        artifacts=finalized.evidence.artifacts,
        cancellation_stop=early_stop,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            finalized.run,
            cancel_intent=early_intent,
            cancellation_stop=early_stop,
            attempts=(replace(current, evidence=early_evidence),),
        )

    assert caught.value.field == "cancel_intent"
    assert caught.value.reason == "before_current_attempt_commit"


def test_cancelled_run_rehydration_rejects_stop_tamper_with_stale_evidence_root() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    finalized, _, _, stop, *_ = _finalized_cancelled_run()
    changed_stop = replace(
        stop,
        sut_access_stopped_at=stop.sut_access_stopped_at + timedelta(seconds=1),
    )
    stale_evidence = replace(
        finalized.evidence,
        cancellation_stop=changed_stop,
    )
    stale_attempt = replace(
        finalized.attempt,
        evidence=stale_evidence,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            finalized.run,
            attempts=(stale_attempt,),
            cancellation_stop=changed_stop,
        )

    assert caught.value.field == "cancellation_stop"
    assert caught.value.reason == "evidence_root_mismatch"


def test_cancelled_run_rehydration_rejects_run_stop_mismatch_with_attempt() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    finalized, _, _, stop, *_ = _finalized_cancelled_run()
    changed_run_stop = replace(
        stop,
        sut_access_stopped_at=stop.sut_access_stopped_at + timedelta(microseconds=1),
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(finalized.run, cancellation_stop=changed_run_stop)

    assert caught.value.field == "cancellation_stop"
    assert caught.value.reason == "attempt_evidence_mismatch"


def test_cancelled_run_rehydration_rejects_passed_downgrade_with_stale_root() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptState, DomainValidationError, RunState
    from qarunner.domain.evidence import EvidenceOutcome

    finalized, *_ = _finalized_cancelled_run()
    stale_evidence = replace(
        finalized.evidence,
        outcome=EvidenceOutcome.PASSED,
    )
    downgraded_attempt = replace(
        finalized.attempt,
        state=AttemptState.PASSED,
        evidence=stale_evidence,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            finalized.run,
            state=RunState.RUNNING,
            current_assignment_id=finalized.attempt.assignment_id,
            attempts=(downgraded_attempt,),
            cancellation_stop=None,
        )

    assert caught.value.field == "attempts"
    assert caught.value.reason == "evidence_root_mismatch"


def test_attempt_rehydration_rejects_terminal_state_without_evidence() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptState, DomainValidationError

    finalized, *_ = _finalized_cancelled_run()

    with pytest.raises(DomainValidationError) as caught:
        replace(
            finalized.attempt,
            state=AttemptState.PASSED,
            evidence=None,
        )

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "evidence"
    assert caught.value.reason == "required_for_terminal"


def test_attempt_rehydration_rejects_evidence_on_nonterminal_state() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import AttemptState, DomainValidationError

    finalized, *_ = _finalized_cancelled_run()
    with pytest.raises(DomainValidationError) as caught:
        replace(finalized.attempt, state=AttemptState.UPLOADING)

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "evidence"
    assert caught.value.reason == "forbidden_for_state"


def test_cancelled_run_rehydration_rejects_malformed_evidence_payload() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError

    finalized, *_ = _finalized_cancelled_run()
    malformed_evidence = replace(
        finalized.evidence,
        artifacts=("bad",),
    )
    malformed_attempt = replace(
        finalized.attempt,
        evidence=malformed_evidence,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(finalized.run, attempts=(malformed_attempt,))

    assert caught.value.field == "cancellation_stop"
    assert caught.value.reason == "evidence_root_mismatch"


def test_postcommit_run_cannot_rehydrate_cancelled_without_stop_evidence() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import DomainValidationError, RunState

    requested, _ = _cancel_requested_uploading_run()

    with pytest.raises(DomainValidationError) as caught:
        replace(
            requested,
            state=RunState.CANCELLED,
            current_assignment_id=None,
        )

    assert caught.value.field == "cancellation_stop"
    assert caught.value.reason == "required_for_postcommit_cancelled"
    assert requested.state is RunState.RUNNING


def test_cancelled_run_rehydration_requires_clear_pointer_and_intent() -> None:
    from dataclasses import replace

    import pytest

    from qarunner.domain import CancellationSource, DomainValidationError, Run, RunState

    finalized, *_ = _finalized_cancelled_run()
    with pytest.raises(DomainValidationError) as current_pointer:
        replace(
            finalized.run,
            current_assignment_id=finalized.attempt.assignment_id,
        )
    assert current_pointer.value.field == "current_assignment_id"
    assert current_pointer.value.reason == "must_be_clear_for_cancelled"

    queued = Run.create(run_id="run-002").transition(RunState.QUEUED, expected_version=0)
    cancelled = queued.request_cancel(
        cancel_key="cancel-002",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop",
        observed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
        expected_version=queued.version,
    )
    with pytest.raises(DomainValidationError) as missing_intent:
        replace(cancelled, cancel_intent=None)
    assert missing_intent.value.field == "cancel_intent"
    assert missing_intent.value.reason == "required_for_cancelled"
