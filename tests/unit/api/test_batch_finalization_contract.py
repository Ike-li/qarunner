"""T-M0-STATE-001H H7a: isolated Batch finalization wire contract."""

import pytest
from pydantic import ValidationError


def test_begin_and_finalize_requests_accept_only_caller_owned_fields() -> None:
    from qarunner.api.batch_finalization_contract import (
        BeginBatchFinalizationRequest,
        FinalizeBatchRequest,
    )

    begin = BeginBatchFinalizationRequest(idempotency_key="begin-001", expected_batch_version=9)
    finalize = FinalizeBatchRequest(idempotency_key="finalize-001", expected_batch_version=10)

    assert begin.schema_version == "qep.begin-batch-finalization-request.v1"
    assert finalize.schema_version == "qep.finalize-batch-request.v1"
    for model in (BeginBatchFinalizationRequest, FinalizeBatchRequest):
        with pytest.raises(ValidationError):
            model(
                idempotency_key="key",
                expected_batch_version=1,
                authority_digest="sha256:" + "0" * 64,
            )
        with pytest.raises(ValidationError):
            model(idempotency_key="key", expected_batch_version=True)


@pytest.mark.parametrize(
    ("status", "state", "reason", "readiness_ref", "basis_ref", "outcome"),
    [
        ("not_ready", "running", "pending_retry", None, None, None),
        ("not_ready", "running", "pending_attempt", None, None, None),
        ("finalizing", "finalizing", None, "readiness-001", None, None),
        ("succeeded", "succeeded", None, "readiness-001", "basis-001", "succeeded"),
        ("failed", "failed", None, "readiness-001", "basis-001", "failed"),
        ("partial", "partial", None, "readiness-001", "basis-001", "partial"),
        ("cancelled", "cancelled", None, "readiness-001", "basis-001", "cancelled"),
    ],
)
def test_projection_enforces_status_specific_safe_reference_matrix(
    status, state, reason, readiness_ref, basis_ref, outcome
) -> None:
    from qarunner.api.batch_finalization_contract import BatchFinalizationProjection

    projection = BatchFinalizationProjection(
        batch_id="batch-001",
        current_state=state,
        current_version=10,
        command_status=status,
        not_ready_reason=reason,
        command_ref="command-001",
        readiness_ref=readiness_ref,
        basis_ref=basis_ref,
        outcome=outcome,
        request_id="request-001",
    )

    assert projection.command_status.value == status


@pytest.mark.parametrize(
    "changes",
    [
        {
            "command_status": "not_ready",
            "current_state": "finalizing",
            "not_ready_reason": "pending_retry",
        },
        {"command_status": "not_ready", "current_state": "running", "not_ready_reason": None},
        {"command_status": "finalizing", "current_state": "finalizing", "readiness_ref": None},
        {"command_status": "finalizing", "current_state": "running", "readiness_ref": "ready-1"},
        {
            "command_status": "succeeded",
            "current_state": "succeeded",
            "readiness_ref": "ready-1",
            "basis_ref": None,
            "outcome": "succeeded",
        },
        {
            "command_status": "failed",
            "current_state": "failed",
            "readiness_ref": "ready-1",
            "basis_ref": "basis-1",
            "outcome": "succeeded",
        },
    ],
)
def test_projection_rejects_cross_status_field_leakage_or_mismatch(changes) -> None:
    from qarunner.api.batch_finalization_contract import BatchFinalizationProjection

    values = {
        "batch_id": "batch-001",
        "current_state": "running",
        "current_version": 9,
        "command_status": "not_ready",
        "not_ready_reason": "pending_retry",
        "command_ref": "command-001",
        "readiness_ref": None,
        "basis_ref": None,
        "outcome": None,
        "request_id": "request-001",
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        BatchFinalizationProjection(**values)


def test_status_outcomes_distinguish_polling_transition_and_terminal() -> None:
    from qarunner.api.batch_finalization_contract import (
        BatchFinalizationStatus,
        finalization_outcome_for_status,
    )

    assert finalization_outcome_for_status(BatchFinalizationStatus.NOT_READY) == (202, True)
    assert finalization_outcome_for_status(BatchFinalizationStatus.FINALIZING) == (202, True)
    for status in (
        BatchFinalizationStatus.SUCCEEDED,
        BatchFinalizationStatus.FAILED,
        BatchFinalizationStatus.PARTIAL,
        BatchFinalizationStatus.CANCELLED,
    ):
        assert finalization_outcome_for_status(status) == (200, False)


def test_problem_contract_is_safe_and_exhaustive() -> None:
    from qarunner.api.batch_finalization_contract import (
        BatchFinalizationProblem,
        BatchFinalizationProblemCode,
        finalization_problem_contract,
    )

    for code in BatchFinalizationProblemCode:
        contract = finalization_problem_contract(code)
        problem = BatchFinalizationProblem(
            code=code,
            message=contract.safe_message,
            request_id="request-001",
            retryable=contract.retryable,
            current_version=9 if code.value == "VERSION_CONFLICT" else None,
        )
        assert problem.message == contract.safe_message
        assert contract.http_status in {401, 403, 404, 409, 422, 500, 503}


@pytest.mark.parametrize(
    "changes",
    [
        {"message": "internal tenant=secret stack trace"},
        {"retryable": True},
        {"current_version": 999},
        {
            "code": "VERSION_CONFLICT",
            "message": "The Batch version changed.",
            "retryable": True,
            "current_version": None,
        },
    ],
)
def test_problem_rejects_mapping_drift_and_internal_version_leak(changes) -> None:
    from qarunner.api.batch_finalization_contract import BatchFinalizationProblem

    values = {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "Authentication required.",
        "request_id": "request-001",
        "retryable": False,
        "current_version": None,
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        BatchFinalizationProblem(**values)


@pytest.mark.parametrize("field", ["command_ref", "readiness_ref", "basis_ref", "request_id"])
def test_projection_rejects_digest_shaped_public_references(field: str) -> None:
    from qarunner.api.batch_finalization_contract import BatchFinalizationProjection

    values = {
        "batch_id": "batch-001",
        "current_state": "succeeded",
        "current_version": 11,
        "command_status": "succeeded",
        "not_ready_reason": None,
        "command_ref": "command-001",
        "readiness_ref": "readiness-001",
        "basis_ref": "basis-001",
        "outcome": "succeeded",
        "request_id": "request-001",
    }
    values[field] = "sha256:" + "a" * 64
    with pytest.raises(ValidationError):
        BatchFinalizationProjection(**values)


def test_wire_schema_never_exposes_internal_authority_or_digest_inputs() -> None:
    import json

    from qarunner.api.batch_finalization_contract import (
        BatchFinalizationProblem,
        BatchFinalizationProjection,
        BeginBatchFinalizationRequest,
        FinalizeBatchRequest,
    )

    schema = json.dumps(
        {
            model.__name__: model.model_json_schema()
            for model in (
                BeginBatchFinalizationRequest,
                FinalizeBatchRequest,
                BatchFinalizationProjection,
                BatchFinalizationProblem,
            )
        },
        sort_keys=True,
    )
    for forbidden in (
        "authority_digest",
        "write_epoch",
        "run_basis_digest",
        "success_policy_digest",
        "candidate_basis",
    ):
        assert forbidden not in schema
