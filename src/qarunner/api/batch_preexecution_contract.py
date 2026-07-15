"""Isolated M0 wire contracts for Batch pre-execution capabilities.

This module deliberately defines no URI, route, transport adapter, or runtime
dependency.  It freezes only the signed capability envelopes and outcomes.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

StrictVersion = Annotated[int, Field(strict=True, ge=0, title="Expected Batch Version")]
CurrentVersion = Annotated[int, Field(strict=True, ge=0)]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
CancellationReason = Annotated[
    str,
    StringConstraints(min_length=1, max_length=256, pattern=r"^[\x20-\x7E]+$"),
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BatchCancellationRequest(_ClosedModel):
    """Only fields owned by a public cancellation caller."""

    schema_version: Literal["qep.batch-cancellation-request.v1"] = (
        "qep.batch-cancellation-request.v1"
    )
    idempotency_key: Identifier
    expected_batch_version: StrictVersion
    reason: CancellationReason


class CommandStatus(StrEnum):
    INTENT_RECORDED = "intent_recorded"
    CLOSURE_PENDING = "closure_pending"
    MATERIALIZED_SCOPE_HANDOFF = "materialized_scope_handoff"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class BatchCancellationAccepted(_ClosedModel):
    """Server-derived accepted/terminal projection envelope."""

    schema_version: Literal["qep.batch-command-projection.v1"] = "qep.batch-command-projection.v1"
    batch_id: Identifier
    current_state: Identifier
    current_version: CurrentVersion
    command_status: CommandStatus
    command_ref: Identifier
    convergence_ref: Identifier | None
    basis_ref: Identifier | None
    request_id: Identifier


@dataclass(frozen=True, slots=True)
class StatusOutcome:
    http_status: int
    retryable: bool


_STATUS_OUTCOMES = {
    CommandStatus.INTENT_RECORDED: StatusOutcome(http_status=202, retryable=True),
    CommandStatus.CLOSURE_PENDING: StatusOutcome(http_status=202, retryable=True),
    CommandStatus.MATERIALIZED_SCOPE_HANDOFF: StatusOutcome(http_status=202, retryable=True),
    CommandStatus.CANCELLED: StatusOutcome(http_status=200, retryable=False),
    CommandStatus.REJECTED: StatusOutcome(http_status=200, retryable=False),
}


def outcome_for_status(status: CommandStatus) -> StatusOutcome:
    return _STATUS_OUTCOMES[status]


class ProblemCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    OBJECT_FORBIDDEN = "OBJECT_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    STATE_CONFLICT = "STATE_CONFLICT"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ProblemContract:
    http_status: int
    retryable: bool
    safe_message: str


_PROBLEM_CONTRACTS = {
    ProblemCode.AUTHENTICATION_REQUIRED: ProblemContract(401, False, "Authentication required."),
    ProblemCode.OBJECT_FORBIDDEN: ProblemContract(403, False, "Access denied."),
    ProblemCode.NOT_FOUND: ProblemContract(404, False, "Resource not found."),
    ProblemCode.VALIDATION_FAILED: ProblemContract(422, False, "Request validation failed."),
    ProblemCode.IDEMPOTENCY_CONFLICT: ProblemContract(409, False, "Request key conflict."),
    ProblemCode.VERSION_CONFLICT: ProblemContract(409, True, "The Batch version changed."),
    ProblemCode.STATE_CONFLICT: ProblemContract(409, False, "The Batch state conflicts."),
    ProblemCode.INTEGRITY_FAILURE: ProblemContract(500, False, "Integrity verification failed."),
    ProblemCode.TEMPORARILY_UNAVAILABLE: ProblemContract(
        503, True, "Service temporarily unavailable."
    ),
}


def problem_contract(code: ProblemCode) -> ProblemContract:
    """Return the safe default mapping; reason-specific policy may only narrow retryability."""

    return _PROBLEM_CONTRACTS[code]


class BatchPreexecutionProblem(_ClosedModel):
    """Versioned safe problem envelope with no diagnostic or winner fields."""

    schema_version: Literal["qep.batch-preexecution-problem.v1"] = (
        "qep.batch-preexecution-problem.v1"
    )
    code: ProblemCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    request_id: Identifier
    retryable: bool
    current_version: CurrentVersion | None = None
