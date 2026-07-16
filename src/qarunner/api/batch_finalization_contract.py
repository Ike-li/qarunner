"""Isolated M0 wire contracts for Batch finalization capabilities."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
OpaqueReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$"),
]
StrictVersion = Annotated[int, Field(strict=True, ge=0)]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BeginBatchFinalizationRequest(_ClosedModel):
    schema_version: Literal["qep.begin-batch-finalization-request.v1"] = (
        "qep.begin-batch-finalization-request.v1"
    )
    idempotency_key: Identifier
    expected_batch_version: StrictVersion


class FinalizeBatchRequest(_ClosedModel):
    schema_version: Literal["qep.finalize-batch-request.v1"] = "qep.finalize-batch-request.v1"
    idempotency_key: Identifier
    expected_batch_version: StrictVersion


class BatchFinalizationStatus(StrEnum):
    NOT_READY = "not_ready"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class BatchFinalizationNotReadyReason(StrEnum):
    PENDING_RETRY = "pending_retry"
    PENDING_ATTEMPT = "pending_attempt"


_TERMINALS = frozenset(
    {
        BatchFinalizationStatus.SUCCEEDED,
        BatchFinalizationStatus.FAILED,
        BatchFinalizationStatus.PARTIAL,
        BatchFinalizationStatus.CANCELLED,
    }
)


class BatchFinalizationProjection(_ClosedModel):
    schema_version: Literal["qep.batch-finalization-projection.v1"] = (
        "qep.batch-finalization-projection.v1"
    )
    batch_id: Identifier
    current_state: Identifier
    current_version: StrictVersion
    command_status: BatchFinalizationStatus
    not_ready_reason: BatchFinalizationNotReadyReason | None
    command_ref: OpaqueReference
    readiness_ref: OpaqueReference | None
    basis_ref: OpaqueReference | None
    outcome: BatchFinalizationStatus | None
    request_id: OpaqueReference

    @model_validator(mode="after")
    def validate_status_matrix(self) -> "BatchFinalizationProjection":
        if self.command_status is BatchFinalizationStatus.NOT_READY:
            if (
                self.current_state != "running"
                or self.not_ready_reason is None
                or self.readiness_ref is not None
                or self.basis_ref is not None
                or self.outcome is not None
            ):
                raise ValueError("not_ready projection field mismatch")
            return self
        if self.command_status is BatchFinalizationStatus.FINALIZING:
            if (
                self.current_state != "finalizing"
                or self.not_ready_reason is not None
                or self.readiness_ref is None
                or self.basis_ref is not None
                or self.outcome is not None
            ):
                raise ValueError("finalizing projection field mismatch")
            return self
        if (
            self.command_status not in _TERMINALS
            or self.current_state != self.command_status.value
            or self.not_ready_reason is not None
            or self.readiness_ref is None
            or self.basis_ref is None
            or self.outcome is not self.command_status
        ):
            raise ValueError("terminal projection field mismatch")
        return self


_STATUS_OUTCOMES = {
    BatchFinalizationStatus.NOT_READY: (202, True),
    BatchFinalizationStatus.FINALIZING: (202, True),
    BatchFinalizationStatus.SUCCEEDED: (200, False),
    BatchFinalizationStatus.FAILED: (200, False),
    BatchFinalizationStatus.PARTIAL: (200, False),
    BatchFinalizationStatus.CANCELLED: (200, False),
}


def finalization_outcome_for_status(status: BatchFinalizationStatus) -> tuple[int, bool]:
    return _STATUS_OUTCOMES[status]


class BatchFinalizationProblemCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    OBJECT_FORBIDDEN = "OBJECT_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    STATE_CONFLICT = "STATE_CONFLICT"
    AUTHORITY_SUPERSEDED = "AUTHORITY_SUPERSEDED"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class BatchFinalizationProblemContract:
    http_status: int
    retryable: bool
    safe_message: str


_PROBLEM_CONTRACTS = {
    BatchFinalizationProblemCode.AUTHENTICATION_REQUIRED: BatchFinalizationProblemContract(
        401, False, "Authentication required."
    ),
    BatchFinalizationProblemCode.OBJECT_FORBIDDEN: BatchFinalizationProblemContract(
        403, False, "Access denied."
    ),
    BatchFinalizationProblemCode.NOT_FOUND: BatchFinalizationProblemContract(
        404, False, "Resource not found."
    ),
    BatchFinalizationProblemCode.VALIDATION_FAILED: BatchFinalizationProblemContract(
        422, False, "Request validation failed."
    ),
    BatchFinalizationProblemCode.IDEMPOTENCY_CONFLICT: BatchFinalizationProblemContract(
        409, False, "Request key conflict."
    ),
    BatchFinalizationProblemCode.VERSION_CONFLICT: BatchFinalizationProblemContract(
        409, True, "The Batch version changed."
    ),
    BatchFinalizationProblemCode.STATE_CONFLICT: BatchFinalizationProblemContract(
        409, False, "The Batch state conflicts."
    ),
    BatchFinalizationProblemCode.AUTHORITY_SUPERSEDED: BatchFinalizationProblemContract(
        409, False, "Finalization authority is no longer current."
    ),
    BatchFinalizationProblemCode.INTEGRITY_FAILURE: BatchFinalizationProblemContract(
        500, False, "Integrity verification failed."
    ),
    BatchFinalizationProblemCode.TEMPORARILY_UNAVAILABLE: BatchFinalizationProblemContract(
        503, True, "Service temporarily unavailable."
    ),
}


def finalization_problem_contract(
    code: BatchFinalizationProblemCode,
) -> BatchFinalizationProblemContract:
    return _PROBLEM_CONTRACTS[code]


class BatchFinalizationProblem(_ClosedModel):
    schema_version: Literal["qep.batch-finalization-problem.v1"] = (
        "qep.batch-finalization-problem.v1"
    )
    code: BatchFinalizationProblemCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    request_id: OpaqueReference
    retryable: bool
    current_version: StrictVersion | None = None

    @model_validator(mode="after")
    def validate_safe_problem_mapping(self) -> "BatchFinalizationProblem":
        contract = finalization_problem_contract(self.code)
        if self.message != contract.safe_message or self.retryable is not contract.retryable:
            raise ValueError("problem payload does not match safe contract")
        version_conflict = self.code is BatchFinalizationProblemCode.VERSION_CONFLICT
        if version_conflict != (self.current_version is not None):
            raise ValueError("current_version does not match problem code")
        return self
