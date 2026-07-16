"""Isolated M0 wire contracts for Run retry capabilities.

No URI, route, authentication adapter, persistence model, or runtime behavior
is defined here.  The models freeze only caller-owned requests and safe
server-derived projections.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
StrictVersion = Annotated[int, Field(strict=True, ge=0)]
PositiveInteger = Annotated[int, Field(strict=True, ge=1)]
ReasonCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$"),
]
DigestText = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRetryQueueRequest(_ClosedModel):
    """Caller-owned request to evaluate and queue one Run retry."""

    schema_version: Literal["qep.run-retry-queue-request.v1"] = "qep.run-retry-queue-request.v1"
    idempotency_key: Identifier
    expected_run_version: StrictVersion
    expected_attempt_version: StrictVersion
    reason_code: ReasonCode


class RetryCommitStartRequest(_ClosedModel):
    """Worker request fields that are not server-derived authority facts."""

    schema_version: Literal["qep.retry-commit-start-request.v1"] = (
        "qep.retry-commit-start-request.v1"
    )
    assignment_id: Identifier
    start_commit_key: Identifier
    expected_run_version: StrictVersion
    expected_attempt_version: StrictVersion
    execution_spec_digest: DigestText
    worker_nonce: Identifier


class RetryCommandStatus(StrEnum):
    RETRY_QUEUED = "retry_queued"
    CLOSED_NO_RETRY = "closed_no_retry"
    START_COMMITTED = "start_committed"


class RetryQueueAccepted(_ClosedModel):
    """Safe queue projection; internal authority participants remain opaque."""

    schema_version: Literal["qep.run-retry-queue-projection.v1"] = (
        "qep.run-retry-queue-projection.v1"
    )
    run_id: Identifier
    current_phase: Identifier
    current_disposition: Identifier
    current_version: StrictVersion
    command_status: Literal[
        RetryCommandStatus.RETRY_QUEUED,
        RetryCommandStatus.CLOSED_NO_RETRY,
    ]
    retry_intent_ref: Identifier | None
    queue_receipt_ref: Identifier | None
    request_id: Identifier

    @model_validator(mode="after")
    def validate_retry_refs(self) -> "RetryQueueAccepted":
        retrying = self.command_status is RetryCommandStatus.RETRY_QUEUED
        if retrying != (self.retry_intent_ref is not None):
            raise ValueError("retry_intent_ref does not match command_status")
        if retrying != (self.queue_receipt_ref is not None):
            raise ValueError("queue_receipt_ref does not match command_status")
        return self


class RetryCommitStarted(_ClosedModel):
    """Safe projection of the unique Attempt/fence created by commit-start."""

    schema_version: Literal["qep.retry-commit-start-projection.v1"] = (
        "qep.retry-commit-start-projection.v1"
    )
    run_id: Identifier
    attempt_id: Identifier
    attempt_no: PositiveInteger
    fence: PositiveInteger
    current_version: StrictVersion
    retry_intent_ref: Identifier
    start_commit_ref: Identifier
    command_status: Literal[RetryCommandStatus.START_COMMITTED] = (
        RetryCommandStatus.START_COMMITTED
    )
    request_id: Identifier


@dataclass(frozen=True, slots=True)
class RetryStatusOutcome:
    http_status: int
    retryable: bool


_STATUS_OUTCOMES = {
    RetryCommandStatus.RETRY_QUEUED: RetryStatusOutcome(202, True),
    RetryCommandStatus.CLOSED_NO_RETRY: RetryStatusOutcome(200, False),
    RetryCommandStatus.START_COMMITTED: RetryStatusOutcome(200, False),
}


def retry_outcome_for_status(status: RetryCommandStatus) -> RetryStatusOutcome:
    return _STATUS_OUTCOMES[status]


class RetryProblemCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    OBJECT_FORBIDDEN = "OBJECT_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    STATE_CONFLICT = "STATE_CONFLICT"
    AUTHORITY_SUPERSEDED = "AUTHORITY_SUPERSEDED"
    RETRY_NOT_ALLOWED = "RETRY_NOT_ALLOWED"
    RETRY_RECEIPT_CONFLICT = "RETRY_RECEIPT_CONFLICT"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RetryProblemContract:
    http_status: int
    retryable: bool
    safe_message: str


_PROBLEM_CONTRACTS = {
    RetryProblemCode.AUTHENTICATION_REQUIRED: RetryProblemContract(
        401, False, "Authentication required."
    ),
    RetryProblemCode.OBJECT_FORBIDDEN: RetryProblemContract(403, False, "Access denied."),
    RetryProblemCode.NOT_FOUND: RetryProblemContract(404, False, "Resource not found."),
    RetryProblemCode.VALIDATION_FAILED: RetryProblemContract(
        422, False, "Request validation failed."
    ),
    RetryProblemCode.IDEMPOTENCY_CONFLICT: RetryProblemContract(
        409, False, "Request key conflict."
    ),
    RetryProblemCode.VERSION_CONFLICT: RetryProblemContract(409, True, "The Run version changed."),
    RetryProblemCode.STATE_CONFLICT: RetryProblemContract(409, False, "The Run state conflicts."),
    RetryProblemCode.AUTHORITY_SUPERSEDED: RetryProblemContract(
        409, False, "Retry authority is no longer current."
    ),
    RetryProblemCode.RETRY_NOT_ALLOWED: RetryProblemContract(409, False, "Retry is not allowed."),
    RetryProblemCode.RETRY_RECEIPT_CONFLICT: RetryProblemContract(
        409, False, "Retry receipt conflicts."
    ),
    RetryProblemCode.INTEGRITY_FAILURE: RetryProblemContract(
        500, False, "Integrity verification failed."
    ),
    RetryProblemCode.TEMPORARILY_UNAVAILABLE: RetryProblemContract(
        503, True, "Service temporarily unavailable."
    ),
}


def retry_problem_contract(code: RetryProblemCode) -> RetryProblemContract:
    return _PROBLEM_CONTRACTS[code]


class RunRetryProblem(_ClosedModel):
    """Versioned safe problem envelope with no internal winner or digest fields."""

    schema_version: Literal["qep.run-retry-problem.v1"] = "qep.run-retry-problem.v1"
    code: RetryProblemCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    request_id: Identifier
    retryable: bool
    current_version: StrictVersion | None = None
