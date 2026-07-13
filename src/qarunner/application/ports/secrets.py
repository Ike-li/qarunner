"""Opaque secret-delivery broker port."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ensure_utc
from qarunner.domain.authority import AttemptAuthority


@dataclass(frozen=True, slots=True)
class SecretRequest:
    """Authority- and purpose-bound request for one opaque secret reference."""

    secret_ref: str
    secret_version: str
    run_id: str
    attempt_id: str
    authority: AttemptAuthority
    target_id: str
    purpose: str
    requested_expires_at: datetime

    def __post_init__(self) -> None:
        for field, value in (
            ("secret_ref", self.secret_ref),
            ("secret_version", self.secret_version),
            ("run_id", self.run_id),
            ("attempt_id", self.attempt_id),
            ("target_id", self.target_id),
            ("purpose", self.purpose),
        ):
            if not isinstance(value, str):
                raise PortContractError(
                    resource="secret_request",
                    field=field,
                    reason="not_string",
                )
            if not value.strip():
                raise PortContractError(
                    resource="secret_request",
                    field=field,
                    reason="empty",
                )
        if not isinstance(self.authority, AttemptAuthority):
            raise PortContractError(
                resource="secret_request",
                field="authority",
                reason="not_attempt_authority",
            )
        ensure_utc(
            resource="secret_request",
            field="requested_expires_at",
            value=self.requested_expires_at,
        )


@dataclass(frozen=True, slots=True)
class SecretDeliveryLease:
    """Opaque delivery capability with no secret material in the control plane."""

    delivery_handle: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.delivery_handle, str):
            raise PortContractError(
                resource="secret_delivery_lease",
                field="delivery_handle",
                reason="not_string",
            )
        if not self.delivery_handle.strip():
            raise PortContractError(
                resource="secret_delivery_lease",
                field="delivery_handle",
                reason="empty",
            )
        ensure_utc(
            resource="secret_delivery_lease",
            field="expires_at",
            value=self.expires_at,
        )


@runtime_checkable
class SecretBroker(Protocol):
    """Issue short-lived opaque delivery capabilities, never plaintext secrets."""

    async def issue(self, request: SecretRequest) -> SecretDeliveryLease: ...

    async def revoke(self, delivery_handle: str) -> None: ...
