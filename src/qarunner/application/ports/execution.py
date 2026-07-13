"""Execution coordination ports that do not expose runtime engine objects."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import (
    PortContractError,
    ReplayResult,
    ensure_utc,
)
from qarunner.domain.authority import AttemptAuthority
from qarunner.domain.digest import Digest, canonical_digest


@dataclass(frozen=True, slots=True)
class ExecutionStopRequest:
    """Authority-bound request to coordinate stopping one current Attempt."""

    request_id: str
    run_id: str
    attempt_id: str
    authority: AttemptAuthority
    cancellation_intent_digest: Digest

    def __post_init__(self) -> None:
        for field, value in (
            ("request_id", self.request_id),
            ("run_id", self.run_id),
            ("attempt_id", self.attempt_id),
        ):
            if not isinstance(value, str):
                raise PortContractError(
                    resource="execution_stop_request",
                    field=field,
                    reason="not_string",
                )
            if not value.strip():
                raise PortContractError(
                    resource="execution_stop_request",
                    field=field,
                    reason="empty",
                )
        if not isinstance(self.authority, AttemptAuthority):
            raise PortContractError(
                resource="execution_stop_request",
                field="authority",
                reason="not_attempt_authority",
            )
        if not isinstance(self.cancellation_intent_digest, Digest):
            raise PortContractError(
                resource="execution_stop_request",
                field="cancellation_intent_digest",
                reason="not_digest",
            )

    @property
    def digest(self) -> Digest:
        """Bind replay identity to the complete stop intent and current authority."""
        return canonical_digest(
            schema_version="qep.execution-stop-request.v1",
            payload={
                "request_id": self.request_id,
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "fence": self.authority.current_fence,
                "worker": {
                    "worker_id": self.authority.current_worker.worker_id,
                    "generation": self.authority.current_worker.generation,
                },
                "cancellation_intent_digest": self.cancellation_intent_digest.value,
            },
        )


@dataclass(frozen=True, slots=True)
class ExecutionStopReceipt:
    """Receipt for an accepted request, not proof that execution stopped."""

    request_id: str
    request_digest: Digest
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str):
            raise PortContractError(
                resource="execution_stop_receipt",
                field="request_id",
                reason="not_string",
            )
        if not self.request_id.strip():
            raise PortContractError(
                resource="execution_stop_receipt",
                field="request_id",
                reason="empty",
            )
        if not isinstance(self.request_digest, Digest):
            raise PortContractError(
                resource="execution_stop_receipt",
                field="request_digest",
                reason="not_digest",
            )
        ensure_utc(
            resource="execution_stop_receipt",
            field="recorded_at",
            value=self.recorded_at,
        )


@runtime_checkable
class ExecutionStopControl(Protocol):
    """Coordinate a stop request without claiming a runtime outcome."""

    async def request_stop(
        self, request: ExecutionStopRequest
    ) -> ReplayResult[ExecutionStopReceipt]: ...
