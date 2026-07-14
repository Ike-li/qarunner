"""T-M0-STATE-001F: fail-closed Batch pre-execution rehydration."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-rehydration.v1",
        payload={"label": label},
    )


def _preplan_snapshot(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot

    return BatchPreexecutionSnapshot(
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
        submission_digest=_digest("submission"),
        preplan_scope_digest=_digest("preplan-scope"),
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
        materialized_run_absence_digest=_digest("no-materialized-runs"),
        execution_absence_snapshot_digest=_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=(),
        item_coverage_proof_digest=None,
    )


def _rejection(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
    )

    return BatchRejection(
        rejection_id="rejection-001",
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
    )


def _intent(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        CancellationSource,
    )

    return BatchCancellationIntent(
        batch_id=batch_id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source_batch_version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
        authorization_digest=_digest("cancel-authorization"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
    )


def _legal_rejected_batch():
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    return rejection, rejected


def _legal_cancelled_batch():
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(batch_id=source.id, source_batch_version=source.version)
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
    )
    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )
    return intent, requested, cancelled


@pytest.mark.parametrize(
    "field",
    [
        pytest.param("rejection_fact", id="rejection-fact"),
        pytest.param("cancellation_intent", id="cancellation-intent"),
        pytest.param("preexecution_closure_basis", id="closure-basis"),
    ],
)
def test_batch_rehydration_rejects_non_domain_fact_types(field: str) -> None:
    """Opaque values cannot enter trusted pre-execution fact slots."""
    from qarunner.domain import Batch, DomainValidationError

    source = Batch.create(batch_id="batch-001")

    with pytest.raises(DomainValidationError) as caught:
        replace(source, **{field: object()})

    assert caught.value.entity_type == "batch"
    assert caught.value.field == field


def test_rejection_fact_cannot_be_rehydrated_without_its_terminal_basis() -> None:
    """A persisted rejection must retain the basis that authorized its outcome."""
    from qarunner.domain import DomainValidationError

    rejection, rejected = _legal_rejected_batch()

    with pytest.raises(DomainValidationError):
        replace(rejected, preexecution_closure_basis=None)

    assert rejected.rejection_fact is rejection
    assert rejected.preexecution_closure_basis is not None


@pytest.mark.parametrize(
    ("family", "drift"),
    [
        pytest.param("rejection", "batch-id", id="rejection-batch-id"),
        pytest.param("rejection", "outcome", id="rejection-outcome"),
        pytest.param("rejection", "version", id="rejection-version"),
        pytest.param("rejection", "command-digest", id="rejection-command"),
        pytest.param("cancel", "batch-id", id="cancel-batch-id"),
        pytest.param("cancel", "outcome", id="cancel-outcome"),
        pytest.param("cancel", "version", id="cancel-version"),
        pytest.param("cancel", "command-digest", id="cancel-command"),
    ],
)
def test_terminal_basis_identity_must_match_rehydrated_batch(
    family: str,
    drift: str,
) -> None:
    """A valid basis object cannot be transplanted or rebound on rehydrate."""
    from qarunner.domain import BatchState, DomainValidationError

    terminal = _legal_rejected_batch()[1] if family == "rejection" else _legal_cancelled_batch()[2]
    basis = terminal.preexecution_closure_basis
    assert basis is not None

    with pytest.raises(DomainValidationError):
        if drift == "batch-id":
            replace(
                terminal,
                preexecution_closure_basis=replace(basis, batch_id="batch-foreign"),
            )
        elif drift == "outcome":
            replace(
                terminal,
                state=(BatchState.FAILED if family == "rejection" else BatchState.PARTIAL),
            )
        elif drift == "version":
            replace(terminal, version=terminal.version + 1)
        elif family == "rejection":
            replace(
                terminal,
                preexecution_closure_basis=replace(
                    basis,
                    rejection_fact_digest=_digest("different-rejection"),
                ),
            )
        else:
            replace(
                terminal,
                preexecution_closure_basis=replace(
                    basis,
                    batch_cancellation_intent_digest=_digest("different-cancel-intent"),
                ),
            )

    assert terminal.preexecution_closure_basis is basis


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param("missing-rejection", id="missing-rejection"),
        pytest.param("cancellation-intent", id="cancellation-intent-present"),
    ],
)
def test_rejection_basis_requires_only_its_stored_rejection(tamper: str) -> None:
    """A REJECTION basis cannot be detached or mixed with cancellation."""
    from qarunner.domain import DomainValidationError

    _, rejected = _legal_rejected_batch()
    intent = _intent(batch_id=rejected.id, source_batch_version=3)

    with pytest.raises(DomainValidationError):
        if tamper == "missing-rejection":
            replace(rejected, rejection_fact=None)
        else:
            replace(rejected, cancellation_intent=intent)

    assert rejected.rejection_fact is not None
    assert rejected.cancellation_intent is None


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param("missing-intent", id="missing-intent"),
        pytest.param("changed-intent", id="changed-intent"),
        pytest.param("rejection-fact", id="rejection-fact-present"),
    ],
)
def test_prestart_cancel_basis_requires_only_its_matching_intent(tamper: str) -> None:
    """PRESTART_CANCEL cannot lose/rebind intent or mix in a rejection fact."""
    from qarunner.domain import DomainValidationError

    intent, _, cancelled = _legal_cancelled_batch()

    with pytest.raises(DomainValidationError):
        if tamper == "missing-intent":
            replace(cancelled, cancellation_intent=None)
        elif tamper == "changed-intent":
            replace(
                cancelled,
                cancellation_intent=replace(intent, reason="different cancellation"),
            )
        else:
            replace(
                cancelled,
                rejection_fact=_rejection(
                    batch_id=cancelled.id,
                    source_batch_version=3,
                ),
            )

    assert cancelled.cancellation_intent is intent
    assert cancelled.rejection_fact is None


def test_recorded_cancel_intent_without_terminal_basis_remains_rehydratable() -> None:
    """Intent acceptance is a legal intermediate state, not a terminal claim."""
    from qarunner.domain import BatchState

    intent, requested, _ = _legal_cancelled_batch()

    rehydrated = replace(requested)

    assert rehydrated.state is BatchState.COLLECTING
    assert rehydrated.version == 4
    assert rehydrated.cancellation_intent is intent
    assert rehydrated.preexecution_closure_basis is None


def test_pending_cancel_scope_kind_must_match_rehydrated_batch_phase() -> None:
    """A pre-plan phase cannot rehydrate a frozen-plan cancellation intent."""
    from qarunner.domain import (
        Batch,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchState,
        DomainValidationError,
    )

    intent = _intent(batch_id="batch-001", source_batch_version=3)
    frozen_plan_intent = replace(
        intent,
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.FROZEN_PLAN,
            preplan_scope_digest=None,
            manifest_digest=_digest("manifest"),
            shard_plan_version=7,
            shard_plan_digest=_digest("shard-plan"),
            canonical_run_set_digest=_digest("canonical-run-set"),
        ),
    )

    with pytest.raises(DomainValidationError) as caught:
        Batch(
            id="batch-001",
            state=BatchState.COLLECTING,
            version=4,
            cancellation_intent=frozen_plan_intent,
        )

    assert caught.value.entity_type == "batch_cancellation_intent"
    assert caught.value.field == "scope"
    assert caught.value.reason == "source_phase_mismatch"


@pytest.mark.parametrize(
    ("state_name", "version"),
    [
        pytest.param("REJECTED", 4, id="terminal-rejected"),
        pytest.param("COLLECTING", 5, id="advanced-nonterminal"),
    ],
)
def test_cancel_intent_without_closure_basis_is_only_valid_as_next_version_pending(
    state_name: str,
    version: int,
) -> None:
    """A stored v1 intent cannot authorize a terminal or survive later advancement."""
    from qarunner.domain import Batch, BatchState, DomainValidationError

    intent = _intent(batch_id="batch-001", source_batch_version=3)

    with pytest.raises(DomainValidationError) as caught:
        Batch(
            id="batch-001",
            state=BatchState[state_name],
            version=version,
            cancellation_intent=intent,
        )

    assert caught.value.field == "cancellation_intent"
    assert caught.value.reason == "aggregate_mismatch"


def test_rejection_stage_must_match_terminal_basis_source_phase() -> None:
    """Digest-consistent facts still cannot claim another rejection phase."""
    from qarunner.domain import (
        BatchRejectionStage,
        DomainValidationError,
    )

    rejection, rejected = _legal_rejected_batch()
    basis = rejected.preexecution_closure_basis
    assert basis is not None
    changed_rejection = replace(rejection, stage=BatchRejectionStage.COLLECTION)
    changed_basis = replace(
        basis,
        rejection_fact_digest=changed_rejection.digest,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            rejected,
            rejection_fact=changed_rejection,
            preexecution_closure_basis=changed_basis,
        )

    assert caught.value.entity_type == "batch_rejection"
    assert caught.value.field == "stage"
    assert caught.value.reason == "source_phase_mismatch"


def test_cancel_intent_scope_identity_must_match_terminal_basis_scope() -> None:
    """A matching intent digest cannot mask drift from the closure scope."""
    from qarunner.domain import DomainValidationError

    intent, _, cancelled = _legal_cancelled_batch()
    basis = cancelled.preexecution_closure_basis
    assert basis is not None
    changed_intent = replace(
        intent,
        scope=replace(
            intent.scope,
            preplan_scope_digest=_digest("different-preplan-scope"),
        ),
    )
    changed_basis = replace(
        basis,
        batch_cancellation_intent_digest=changed_intent.digest,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            cancelled,
            cancellation_intent=changed_intent,
            preexecution_closure_basis=changed_basis,
        )

    assert caught.value.field == "scope"
    assert caught.value.reason == "cancellation_intent_mismatch"


@pytest.mark.parametrize("drift", ["batch-id", "source-version"])
def test_recorded_cancel_intent_must_belong_to_an_earlier_version_of_this_batch(
    drift: str,
) -> None:
    from qarunner.domain import Batch, BatchState, DomainValidationError

    intent = _intent(
        batch_id=("batch-other" if drift == "batch-id" else "batch-001"),
        source_batch_version=(4 if drift == "source-version" else 3),
    )

    with pytest.raises(DomainValidationError) as caught:
        Batch(
            id="batch-001",
            state=BatchState.COLLECTING,
            version=4,
            cancellation_intent=intent,
        )

    assert caught.value.field == "cancellation_intent"
    assert caught.value.reason == "aggregate_mismatch"


@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("SUCCEEDED", id="succeeded"),
        pytest.param("FAILED", id="failed"),
        pytest.param("PARTIAL", id="partial"),
        pytest.param("CANCELLED", id="cancelled"),
        pytest.param("REJECTED", id="rejected"),
    ],
)
def test_legacy_terminal_without_new_facts_remains_rehydratable(state_name: str) -> None:
    """Pre-migration terminal rows remain readable until backfill is implemented."""
    from qarunner.domain import Batch, BatchState

    legacy = Batch(id="batch-legacy", state=BatchState[state_name], version=9)

    assert legacy.state is BatchState[state_name]
    assert legacy.rejection_fact is None
    assert legacy.cancellation_intent is None
    assert legacy.preexecution_closure_basis is None
