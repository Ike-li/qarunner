"""Frozen M0 wire contracts for Run retry capabilities."""

import pytest
from pydantic import ValidationError


def queue_values():
    return {
        "idempotency_key": "retry-1",
        "expected_run_version": 3,
        "expected_attempt_version": 2,
        "reason_code": "test_failed.allowed",
    }


def commit_values():
    return {
        "assignment_id": "assignment-1",
        "start_commit_key": "commit-1",
        "expected_run_version": 4,
        "expected_attempt_version": 2,
        "execution_spec_digest": "sha256:" + "a" * 64,
        "worker_nonce": "nonce-1",
    }


def test_queue_request_is_closed_and_client_owned() -> None:
    from qarunner.api.run_retry_contract import RunRetryQueueRequest

    request = RunRetryQueueRequest(**queue_values())
    assert request.schema_version == "qep.run-retry-queue-request.v1"
    assert request.model_json_schema()["additionalProperties"] is False
    assert set(request.model_json_schema()["required"]) == set(queue_values())


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "actor_id",
        "policy_digest",
        "authority_digest",
        "budget",
        "proof_digest",
        "acceptance_digest",
        "writer_digest",
        "recorded_at",
    ],
)
def test_queue_request_forbids_server_owned_fields(field) -> None:
    from qarunner.api.run_retry_contract import RunRetryQueueRequest

    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunRetryQueueRequest.model_validate({**queue_values(), field: "attack"})


@pytest.mark.parametrize("field", ["expected_run_version", "expected_attempt_version"])
@pytest.mark.parametrize("value", [True, -1])
def test_queue_versions_are_strict_nonnegative(field, value) -> None:
    from qarunner.api.run_retry_contract import RunRetryQueueRequest

    with pytest.raises(ValidationError):
        RunRetryQueueRequest.model_validate({**queue_values(), field: value})


@pytest.mark.parametrize("reason", ["", "bad reason", "x" * 129])
def test_reason_code_is_bounded_machine_text(reason) -> None:
    from qarunner.api.run_retry_contract import RunRetryQueueRequest

    with pytest.raises(ValidationError):
        RunRetryQueueRequest.model_validate({**queue_values(), "reason_code": reason})


def test_commit_request_forbids_server_owned_attempt_and_authority() -> None:
    from qarunner.api.run_retry_contract import RetryCommitStartRequest

    request = RetryCommitStartRequest(**commit_values())
    assert request.schema_version == "qep.retry-commit-start-request.v1"
    assert request.model_json_schema()["additionalProperties"] is False
    for field in ("attempt_id", "fence", "authority_digest", "queue_receipt", "worker_token"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            RetryCommitStartRequest.model_validate({**commit_values(), field: "attack"})


@pytest.mark.parametrize("field", ["expected_run_version", "expected_attempt_version"])
@pytest.mark.parametrize("value", [False, -1])
def test_commit_versions_are_strict_nonnegative(field, value) -> None:
    from qarunner.api.run_retry_contract import RetryCommitStartRequest

    with pytest.raises(ValidationError):
        RetryCommitStartRequest.model_validate({**commit_values(), field: value})


def test_commit_requires_canonical_sha256_spec_digest() -> None:
    from qarunner.api.run_retry_contract import RetryCommitStartRequest

    with pytest.raises(ValidationError):
        RetryCommitStartRequest.model_validate(
            {**commit_values(), "execution_spec_digest": "sha256:not-canonical"}
        )


def test_safe_queue_and_commit_projections_are_closed() -> None:
    from qarunner.api.run_retry_contract import RetryCommitStarted, RetryQueueAccepted

    queued = RetryQueueAccepted(
        run_id="run-1",
        current_phase="retry_queued",
        current_disposition="retry_queued",
        current_version=4,
        command_status="retry_queued",
        retry_intent_ref="intent-1",
        queue_receipt_ref="receipt-1",
        request_id="request-1",
    )
    started = RetryCommitStarted(
        run_id="run-1",
        attempt_id="attempt-2",
        attempt_no=2,
        fence=2,
        current_version=7,
        retry_intent_ref="intent-1",
        start_commit_ref="commit-1",
        request_id="request-1",
    )
    assert queued.schema_version == "qep.run-retry-queue-projection.v1"
    assert started.command_status.value == "start_committed"
    for model in (queued, started):
        assert model.model_json_schema()["additionalProperties"] is False
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(model).model_validate({**model.model_dump(mode="json"), "authority_digest": "x"})


@pytest.mark.parametrize(
    "changes",
    [
        {"retry_intent_ref": None},
        {"queue_receipt_ref": None},
        {"command_status": "closed_no_retry"},
    ],
)
def test_queue_projection_enforces_status_reference_matrix(changes) -> None:
    from qarunner.api.run_retry_contract import RetryQueueAccepted

    values = {
        "run_id": "run-1",
        "current_phase": "retry_queued",
        "current_disposition": "retry_queued",
        "current_version": 4,
        "command_status": "retry_queued",
        "retry_intent_ref": "intent-1",
        "queue_receipt_ref": "receipt-1",
        "request_id": "request-1",
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        RetryQueueAccepted.model_validate(values)


def test_closed_no_retry_projection_has_no_retry_refs() -> None:
    from qarunner.api.run_retry_contract import RetryQueueAccepted

    projection = RetryQueueAccepted(
        run_id="run-1",
        current_phase="closed",
        current_disposition="closed_no_retry",
        current_version=4,
        command_status="closed_no_retry",
        retry_intent_ref=None,
        queue_receipt_ref=None,
        request_id="request-1",
    )
    assert projection.command_status.value == "closed_no_retry"


@pytest.mark.parametrize(
    ("status", "http_status", "retryable"),
    [
        ("retry_queued", 202, True),
        ("closed_no_retry", 200, False),
        ("start_committed", 200, False),
    ],
)
def test_status_mapping_is_stable(status, http_status, retryable) -> None:
    from qarunner.api.run_retry_contract import RetryCommandStatus, retry_outcome_for_status

    outcome = retry_outcome_for_status(RetryCommandStatus(status))
    assert (outcome.http_status, outcome.retryable) == (http_status, retryable)


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        ("AUTHENTICATION_REQUIRED", 401, False),
        ("OBJECT_FORBIDDEN", 403, False),
        ("NOT_FOUND", 404, False),
        ("VALIDATION_FAILED", 422, False),
        ("IDEMPOTENCY_CONFLICT", 409, False),
        ("VERSION_CONFLICT", 409, True),
        ("STATE_CONFLICT", 409, False),
        ("AUTHORITY_SUPERSEDED", 409, False),
        ("RETRY_NOT_ALLOWED", 409, False),
        ("RETRY_RECEIPT_CONFLICT", 409, False),
        ("INTEGRITY_FAILURE", 500, False),
        ("TEMPORARILY_UNAVAILABLE", 503, True),
    ],
)
def test_problem_mapping_is_stable(code, status, retryable) -> None:
    from qarunner.api.run_retry_contract import RetryProblemCode, retry_problem_contract

    result = retry_problem_contract(RetryProblemCode(code))
    assert (result.http_status, result.retryable) == (status, retryable)
    assert result.safe_message


def test_problem_envelope_is_closed_and_safe() -> None:
    from qarunner.api.run_retry_contract import RunRetryProblem

    problem = RunRetryProblem(
        code="AUTHORITY_SUPERSEDED",
        message="Retry authority is no longer current.",
        request_id="request-1",
        retryable=False,
        current_version=4,
    )
    assert problem.schema_version == "qep.run-retry-problem.v1"
    assert set(problem.model_json_schema()["properties"]) == {
        "schema_version",
        "code",
        "message",
        "request_id",
        "retryable",
        "current_version",
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunRetryProblem.model_validate({**problem.model_dump(mode="json"), "stored_digest": "x"})
