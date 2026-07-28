"""Attempt/target/purpose-bound short-lived secret delivery (M5, T-M5-SECRET-001).

Control plane never stores plaintext secret material. This ledger tracks only
opaque delivery handles bound to (run, attempt, fence, worker, target, purpose)
with a TTL. authorize_use fails closed on binding mismatch, expiry, or revoke.
revoke_all_for_attempt covers Attempt stop / fence invalidation so secrets
cannot be reused after stop (DES §8.3).

``redact_secret_material`` is a logging safety net — not a substitute for never
putting secrets in logs.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


class SecretDeliveryState(enum.StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class SecretDelivery:
    """One opaque delivery capability — no secret bytes in this object."""

    delivery_handle: str
    secret_ref: str
    secret_version: str
    run_id: str
    attempt_id: str
    fence: int
    worker: WorkerRef
    target_id: str
    purpose: str
    issued_at: datetime
    expires_at: datetime
    state: SecretDeliveryState

    def __post_init__(self) -> None:
        entity = "secret_delivery"
        for field in (
            "delivery_handle",
            "secret_ref",
            "secret_version",
            "run_id",
            "attempt_id",
            "target_id",
            "purpose",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
        if isinstance(self.fence, bool) or not isinstance(self.fence, int) or self.fence < 1:
            raise DomainValidationError(entity_type=entity, field="fence", reason="invalid")
        if not isinstance(self.worker, WorkerRef):
            raise DomainValidationError(
                entity_type=entity, field="worker", reason="not_worker_ref"
            )
        for field in ("issued_at", "expires_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime):
                raise DomainValidationError(entity_type=entity, field=field, reason="not_datetime")
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise DomainValidationError(entity_type=entity, field=field, reason="not_utc")
        if self.expires_at <= self.issued_at:
            raise DomainValidationError(
                entity_type=entity, field="expires_at", reason="not_after_issued_at"
            )
        if not isinstance(self.state, SecretDeliveryState):
            raise DomainValidationError(entity_type=entity, field="state", reason="invalid")

    def is_active_at(self, observed_at: datetime) -> bool:
        _require_utc("secret_delivery", "observed_at", observed_at)
        if self.state is not SecretDeliveryState.ACTIVE:
            return False
        return self.issued_at <= observed_at < self.expires_at


@dataclass(frozen=True, slots=True)
class SecretDeliveryLedger:
    """In-memory / pure-domain ledger of opaque secret deliveries."""

    deliveries: tuple[SecretDelivery, ...]

    def __post_init__(self) -> None:
        entity = "secret_delivery_ledger"
        if not isinstance(self.deliveries, tuple) or any(
            not isinstance(item, SecretDelivery) for item in self.deliveries
        ):
            raise DomainValidationError(entity_type=entity, field="deliveries", reason="invalid")
        handles = tuple(item.delivery_handle for item in self.deliveries)
        if len(handles) != len(set(handles)):
            raise DomainValidationError(
                entity_type=entity, field="deliveries", reason="duplicate_handle"
            )

    @classmethod
    def empty(cls) -> SecretDeliveryLedger:
        return cls(deliveries=())

    def delivery_by_handle(self, delivery_handle: str) -> SecretDelivery | None:
        return next(
            (item for item in self.deliveries if item.delivery_handle == delivery_handle),
            None,
        )

    def issue(
        self,
        *,
        delivery_handle: str,
        secret_ref: str,
        secret_version: str,
        run_id: str,
        attempt_id: str,
        fence: int,
        worker: WorkerRef,
        target_id: str,
        purpose: str,
        issued_at: datetime,
        ttl: timedelta,
    ) -> tuple[SecretDeliveryLedger, SecretDelivery]:
        entity = "secret_delivery_ledger"
        if self.delivery_by_handle(delivery_handle) is not None:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="duplicate"
            )
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise DomainValidationError(entity_type=entity, field="ttl", reason="invalid")
        _require_utc(entity, "issued_at", issued_at)
        delivery = SecretDelivery(
            delivery_handle=delivery_handle,
            secret_ref=secret_ref,
            secret_version=secret_version,
            run_id=run_id,
            attempt_id=attempt_id,
            fence=fence,
            worker=worker,
            target_id=target_id,
            purpose=purpose,
            issued_at=issued_at,
            expires_at=issued_at + ttl,
            state=SecretDeliveryState.ACTIVE,
        )
        return (
            SecretDeliveryLedger(deliveries=(*self.deliveries, delivery)),
            delivery,
        )

    def authorize_use(
        self,
        *,
        delivery_handle: str,
        attempt_id: str,
        fence: int,
        target_id: str,
        purpose: str,
        observed_at: datetime,
    ) -> SecretDelivery:
        entity = "secret_delivery_ledger"
        _require_utc(entity, "observed_at", observed_at)
        current = self.delivery_by_handle(delivery_handle)
        if current is None:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="not_found"
            )
        if current.state is SecretDeliveryState.REVOKED:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="revoked"
            )
        if current.state is SecretDeliveryState.EXPIRED or observed_at >= current.expires_at:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="expired"
            )
        # Unreachable once REVOKED/EXPIRED are handled above — only ACTIVE remains
        # in SecretDeliveryState. Kept as defense-in-depth for a future state.
        if current.state is not SecretDeliveryState.ACTIVE:  # pragma: no cover
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="not_active"
            )
        for field, expected, actual in (
            ("attempt_id", current.attempt_id, attempt_id),
            ("fence", current.fence, fence),
            ("target_id", current.target_id, target_id),
            ("purpose", current.purpose, purpose),
        ):
            if expected != actual:
                raise DomainValidationError(
                    entity_type=entity, field=field, reason="binding_mismatch"
                )
        return current

    def revoke(self, *, delivery_handle: str, revoked_at: datetime) -> SecretDeliveryLedger:
        entity = "secret_delivery_ledger"
        _require_utc(entity, "revoked_at", revoked_at)
        current = self.delivery_by_handle(delivery_handle)
        if current is None:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="not_found"
            )
        if current.state is SecretDeliveryState.REVOKED:
            return self
        if current.state is not SecretDeliveryState.ACTIVE:
            raise DomainValidationError(
                entity_type=entity, field="delivery_handle", reason="not_active"
            )
        updated = _with_state(current, SecretDeliveryState.REVOKED)
        return SecretDeliveryLedger(
            deliveries=tuple(
                updated if item.delivery_handle == delivery_handle else item
                for item in self.deliveries
            )
        )

    def revoke_all_for_attempt(
        self,
        *,
        attempt_id: str,
        fence: int,
        revoked_at: datetime,
    ) -> SecretDeliveryLedger:
        entity = "secret_delivery_ledger"
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise DomainValidationError(entity_type=entity, field="attempt_id", reason="invalid")
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
            raise DomainValidationError(entity_type=entity, field="fence", reason="invalid")
        _require_utc(entity, "revoked_at", revoked_at)
        deliveries = tuple(
            _with_state(item, SecretDeliveryState.REVOKED)
            if (
                item.state is SecretDeliveryState.ACTIVE
                and item.attempt_id == attempt_id
                and item.fence == fence
            )
            else item
            for item in self.deliveries
        )
        return SecretDeliveryLedger(deliveries=deliveries)

    def expire_due(self, *, observed_at: datetime) -> SecretDeliveryLedger:
        entity = "secret_delivery_ledger"
        _require_utc(entity, "observed_at", observed_at)
        deliveries = tuple(
            _with_state(item, SecretDeliveryState.EXPIRED)
            if item.state is SecretDeliveryState.ACTIVE and observed_at >= item.expires_at
            else item
            for item in self.deliveries
        )
        return SecretDeliveryLedger(deliveries=deliveries)


def _with_state(delivery: SecretDelivery, state: SecretDeliveryState) -> SecretDelivery:
    return SecretDelivery(
        delivery_handle=delivery.delivery_handle,
        secret_ref=delivery.secret_ref,
        secret_version=delivery.secret_version,
        run_id=delivery.run_id,
        attempt_id=delivery.attempt_id,
        fence=delivery.fence,
        worker=delivery.worker,
        target_id=delivery.target_id,
        purpose=delivery.purpose,
        issued_at=delivery.issued_at,
        expires_at=delivery.expires_at,
        state=state,
    )


def _require_utc(entity: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise DomainValidationError(entity_type=entity, field=field, reason="not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DomainValidationError(entity_type=entity, field=field, reason="not_utc")


_BEARER_RE = re.compile(r"(?i)(Authorization:\s*Bearer\s+)\S+")
_TOKEN_QUERY_RE = re.compile(r"([?&](?:token|access_token|secret|password|api_key)=)[^&\s]+")


def redact_secret_material(
    text: str,
    *,
    handles: tuple[str, ...] = (),
    extra_literals: tuple[str, ...] = (),
) -> str:
    """Scrub known handles/literals and common secret patterns from log text."""
    if not isinstance(text, str):
        raise DomainValidationError(
            entity_type="secret_redaction", field="text", reason="not_string"
        )
    if not isinstance(handles, tuple) or any(not isinstance(h, str) for h in handles):
        raise DomainValidationError(
            entity_type="secret_redaction", field="handles", reason="invalid"
        )
    if not isinstance(extra_literals, tuple) or any(
        not isinstance(item, str) for item in extra_literals
    ):
        raise DomainValidationError(
            entity_type="secret_redaction", field="extra_literals", reason="invalid"
        )
    scrubbed = text
    for handle in handles:
        if handle:
            scrubbed = scrubbed.replace(handle, "[REDACTED]")
    # Longer literals first so partial overlaps redact fully.
    for literal in sorted((item for item in extra_literals if item), key=len, reverse=True):
        scrubbed = scrubbed.replace(literal, "[REDACTED]")
    scrubbed = _BEARER_RE.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _TOKEN_QUERY_RE.sub(r"\1[REDACTED]", scrubbed)
    return scrubbed
