"""Idempotency records for safely replaying command responses."""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.domain.digest import Digest
from qarunner.domain.errors import IdempotencyConflict


@dataclass(frozen=True, slots=True)
class IdempotencyResolution:
    """The original response returned for a recognized replay."""

    response_status: int
    response_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Immutable binding from one scoped key to its request digest and response."""

    scope: str
    key: str
    request_digest: Digest
    response_status: int
    response_ref: str

    @classmethod
    def create(
        cls,
        *,
        scope: str,
        key: str,
        request_digest: Digest,
        response_status: int,
        response_ref: str,
    ) -> IdempotencyRecord:
        """Record the first durable result for a scoped idempotency key."""
        return cls(
            scope=scope,
            key=key,
            request_digest=request_digest,
            response_status=response_status,
            response_ref=response_ref,
        )

    def resolve(self, *, request_digest: Digest) -> IdempotencyResolution:
        """Replay the original response when the request digest matches."""
        if request_digest != self.request_digest:
            raise IdempotencyConflict(
                scope=self.scope,
                key=self.key,
                stored_digest=self.request_digest,
                received_digest=request_digest,
            )
        return IdempotencyResolution(
            response_status=self.response_status,
            response_ref=self.response_ref,
            replayed=True,
        )
