"""Frozen M0 wire contracts for Batch pre-execution capabilities."""

import pytest
from pydantic import ValidationError


def test_cancel_request_accepts_only_client_owned_fields() -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationRequest

    request = BatchCancellationRequest.model_validate(
        {
            "idempotency_key": "cancel-001",
            "expected_batch_version": 3,
            "reason": "operator requested cancellation",
        }
    )

    assert request.model_dump() == {
        "schema_version": "qep.batch-cancellation-request.v1",
        "idempotency_key": "cancel-001",
        "expected_batch_version": 3,
        "reason": "operator requested cancellation",
    }


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "actor_id",
        "project_id",
        "suite_revision_id",
        "source",
        "scope",
        "authorization_digest",
        "recorded_at",
        "snapshot",
        "proof",
    ],
)
def test_cancel_request_forbids_server_derived_and_proof_fields(forbidden_field: str) -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationRequest

    with pytest.raises(ValidationError) as captured:
        BatchCancellationRequest.model_validate(
            {
                "idempotency_key": "cancel-001",
                "expected_batch_version": 3,
                "reason": "operator requested cancellation",
                forbidden_field: "attacker-controlled",
            }
        )

    assert captured.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("version", [True, False, -1])
def test_cancel_request_requires_a_non_negative_strict_integer_version(version: object) -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationRequest

    with pytest.raises(ValidationError):
        BatchCancellationRequest(
            idempotency_key="cancel-001",
            expected_batch_version=version,  # type: ignore[arg-type]
            reason="operator requested cancellation",
        )


@pytest.mark.parametrize("reason", ["", "contains\ncontrol", "x" * 257])
def test_cancel_reason_has_stable_length_and_character_limits(reason: str) -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationRequest

    with pytest.raises(ValidationError):
        BatchCancellationRequest(
            idempotency_key="cancel-001", expected_batch_version=3, reason=reason
        )


def test_request_json_schema_freezes_required_fields_and_closed_object() -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationRequest

    schema = BatchCancellationRequest.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["idempotency_key", "expected_batch_version", "reason"]
    assert set(schema["properties"]) == {
        "schema_version",
        "idempotency_key",
        "expected_batch_version",
        "reason",
    }
    assert schema["properties"]["expected_batch_version"] == {
        "minimum": 0,
        "title": "Expected Batch Version",
        "type": "integer",
    }


@pytest.mark.parametrize(
    ("status", "http_status", "retryable"),
    [
        ("intent_recorded", 202, True),
        ("closure_pending", 202, True),
        ("materialized_scope_handoff", 202, True),
        ("cancelled", 200, False),
        ("rejected", 200, False),
    ],
)
def test_accepted_response_status_has_stable_http_projection(
    status: str, http_status: int, retryable: bool
) -> None:
    from qarunner.api.batch_preexecution_contract import CommandStatus, outcome_for_status

    outcome = outcome_for_status(CommandStatus(status))

    assert outcome.http_status == http_status
    assert outcome.retryable is retryable


def test_accepted_response_forbids_client_or_secret_fields() -> None:
    from qarunner.api.batch_preexecution_contract import BatchCancellationAccepted

    response = BatchCancellationAccepted(
        batch_id="batch-001",
        current_state="cancellation_requested",
        current_version=4,
        command_status="closure_pending",
        command_ref="cancel-001",
        convergence_ref="convergence-001",
        basis_ref=None,
        request_id="request-001",
    )
    assert response.schema_version == "qep.batch-command-projection.v1"
    assert response.command_status.value == "closure_pending"
    with pytest.raises(ValidationError):
        BatchCancellationAccepted.model_validate(
            {**response.model_dump(mode="json"), "winner_digest": "secret"}
        )


@pytest.mark.parametrize(
    ("code", "http_status", "retryable"),
    [
        ("AUTHENTICATION_REQUIRED", 401, False),
        ("OBJECT_FORBIDDEN", 403, False),
        ("NOT_FOUND", 404, False),
        ("VALIDATION_FAILED", 422, False),
        ("IDEMPOTENCY_CONFLICT", 409, False),
        ("VERSION_CONFLICT", 409, True),
        ("STATE_CONFLICT", 409, False),
        ("INTEGRITY_FAILURE", 500, False),
        ("TEMPORARILY_UNAVAILABLE", 503, True),
    ],
)
def test_nine_problem_codes_have_stable_safe_default_mapping(
    code: str, http_status: int, retryable: bool
) -> None:
    from qarunner.api.batch_preexecution_contract import ProblemCode, problem_contract

    contract = problem_contract(ProblemCode(code))

    assert contract.http_status == http_status
    assert contract.retryable is retryable
    assert contract.safe_message


def test_problem_envelope_is_versioned_closed_and_contains_no_unsafe_details() -> None:
    from qarunner.api.batch_preexecution_contract import BatchPreexecutionProblem

    problem = BatchPreexecutionProblem(
        code="VERSION_CONFLICT",
        message="The Batch version changed.",
        request_id="request-001",
        retryable=True,
        current_version=4,
    )
    assert problem.schema_version == "qep.batch-preexecution-problem.v1"
    assert set(problem.model_json_schema()["properties"]) == {
        "schema_version",
        "code",
        "message",
        "request_id",
        "retryable",
        "current_version",
    }
    assert problem.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        BatchPreexecutionProblem.model_validate(
            {**problem.model_dump(mode="json"), "stack": "unsafe"}
        )
