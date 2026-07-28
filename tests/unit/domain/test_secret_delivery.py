"""T-M5-SECRET-001 foundation: Attempt/target/purpose-bound short-lived secrets.

Control plane never stores plaintext. Domain ledger tracks opaque delivery
handles: issue under binding + TTL, authorize_use only for the same binding
while ACTIVE, revoke/stop/expiry forbids reuse. Log redaction is a safety net
(not a substitute for never logging secrets).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    DomainValidationError,
    SecretDeliveryLedger,
    SecretDeliveryState,
    WorkerRef,
    redact_secret_material,
)

T0 = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)
WORKER = WorkerRef(worker_id="worker-001", generation=1)


def _ledger() -> SecretDeliveryLedger:
    return SecretDeliveryLedger.empty()


def _issue(ledger: SecretDeliveryLedger, **changes):
    values = {
        "delivery_handle": "hdl-001",
        "secret_ref": "secret://sut/account-1",
        "secret_version": "v1",
        "run_id": "run-001",
        "attempt_id": "attempt-001",
        "fence": 1,
        "worker": WORKER,
        "target_id": "target-staging-web",
        "purpose": "browser-login",
        "issued_at": T0 + timedelta(minutes=1),
        "ttl": timedelta(minutes=10),
    }
    values.update(changes)
    return ledger.issue(**values)


# ── Issue ────────────────────────────────────────────────────────────────────


def test_issue_creates_active_delivery_bound_to_attempt_target_purpose() -> None:
    ledger, delivery = _issue(_ledger())
    assert delivery.state is SecretDeliveryState.ACTIVE
    assert delivery.attempt_id == "attempt-001"
    assert delivery.fence == 1
    assert delivery.target_id == "target-staging-web"
    assert delivery.purpose == "browser-login"
    assert delivery.expires_at == T0 + timedelta(minutes=11)
    assert delivery.worker == WORKER
    assert ledger.delivery_by_handle("hdl-001") is delivery


def test_issue_rejects_duplicate_delivery_handle() -> None:
    ledger, _ = _issue(_ledger())
    with pytest.raises(DomainValidationError) as caught:
        _issue(ledger, delivery_handle="hdl-001", attempt_id="attempt-002")
    assert caught.value.field == "delivery_handle"
    assert caught.value.reason == "duplicate"


def test_issue_rejects_invalid_ttl_or_fence() -> None:
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), ttl=timedelta(0))
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), fence=0)
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), delivery_handle=" ")


# ── authorize_use: binding + TTL ─────────────────────────────────────────────


def test_authorize_use_allows_matching_active_binding() -> None:
    ledger, delivery = _issue(_ledger())
    authorized = ledger.authorize_use(
        delivery_handle=delivery.delivery_handle,
        attempt_id="attempt-001",
        fence=1,
        target_id="target-staging-web",
        purpose="browser-login",
        observed_at=T0 + timedelta(minutes=2),
    )
    assert authorized.delivery_handle == "hdl-001"
    assert authorized.state is SecretDeliveryState.ACTIVE


def test_authorize_use_rejects_binding_mismatch() -> None:
    ledger, delivery = _issue(_ledger())
    for changes, field in (
        ({"attempt_id": "attempt-other"}, "attempt_id"),
        ({"fence": 2}, "fence"),
        ({"target_id": "target-other"}, "target_id"),
        ({"purpose": "api-login"}, "purpose"),
    ):
        kwargs = {
            "delivery_handle": delivery.delivery_handle,
            "attempt_id": "attempt-001",
            "fence": 1,
            "target_id": "target-staging-web",
            "purpose": "browser-login",
            "observed_at": T0 + timedelta(minutes=2),
        }
        kwargs.update(changes)
        with pytest.raises(DomainValidationError) as caught:
            ledger.authorize_use(**kwargs)
        assert caught.value.field == field
        assert caught.value.reason == "binding_mismatch"


def test_authorize_use_rejects_expired_or_unknown_handle() -> None:
    ledger, delivery = _issue(_ledger(), ttl=timedelta(minutes=5))
    with pytest.raises(DomainValidationError) as expired:
        ledger.authorize_use(
            delivery_handle=delivery.delivery_handle,
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at=T0 + timedelta(minutes=20),
        )
    assert expired.value.reason == "expired"

    with pytest.raises(DomainValidationError) as missing:
        ledger.authorize_use(
            delivery_handle="missing",
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at=T0 + timedelta(minutes=2),
        )
    assert missing.value.reason == "not_found"


# ── revoke / stop → no reuse ─────────────────────────────────────────────────


def test_revoke_forbids_subsequent_use() -> None:
    ledger, delivery = _issue(_ledger())
    ledger = ledger.revoke(
        delivery_handle=delivery.delivery_handle,
        revoked_at=T0 + timedelta(minutes=2),
    )
    assert ledger.delivery_by_handle("hdl-001").state is SecretDeliveryState.REVOKED
    with pytest.raises(DomainValidationError) as caught:
        ledger.authorize_use(
            delivery_handle="hdl-001",
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at=T0 + timedelta(minutes=3),
        )
    assert caught.value.reason == "revoked"


def test_revoke_is_idempotent_for_same_handle() -> None:
    ledger, delivery = _issue(_ledger())
    ledger = ledger.revoke(
        delivery_handle=delivery.delivery_handle,
        revoked_at=T0 + timedelta(minutes=2),
    )
    again = ledger.revoke(
        delivery_handle=delivery.delivery_handle,
        revoked_at=T0 + timedelta(minutes=3),
    )
    assert again.delivery_by_handle("hdl-001").state is SecretDeliveryState.REVOKED


def test_revoke_all_for_attempt_invalidates_every_active_handle() -> None:
    """Attempt stop / fence invalidation: no secret reuse after stop (DES §8.3)."""
    ledger = _ledger()
    ledger, a = _issue(ledger, delivery_handle="hdl-a", purpose="browser-login")
    ledger, b = _issue(
        ledger,
        delivery_handle="hdl-b",
        purpose="api-login",
        secret_ref="secret://sut/token",
    )
    # Different attempt remains.
    ledger, other = _issue(
        ledger,
        delivery_handle="hdl-other",
        attempt_id="attempt-002",
        fence=1,
    )
    ledger = ledger.revoke_all_for_attempt(
        attempt_id="attempt-001",
        fence=1,
        revoked_at=T0 + timedelta(minutes=5),
    )
    assert ledger.delivery_by_handle("hdl-a").state is SecretDeliveryState.REVOKED
    assert ledger.delivery_by_handle("hdl-b").state is SecretDeliveryState.REVOKED
    assert ledger.delivery_by_handle("hdl-other").state is SecretDeliveryState.ACTIVE
    with pytest.raises(DomainValidationError):
        ledger.authorize_use(
            delivery_handle="hdl-a",
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at=T0 + timedelta(minutes=6),
        )


def test_revoke_all_for_attempt_is_fence_scoped() -> None:
    """A bumped fence (retry) must not keep secrets issued for the prior fence."""
    ledger = _ledger()
    ledger, old = _issue(ledger, delivery_handle="hdl-old", fence=1)
    ledger, new = _issue(ledger, delivery_handle="hdl-new", fence=2)
    ledger = ledger.revoke_all_for_attempt(
        attempt_id="attempt-001",
        fence=1,
        revoked_at=T0 + timedelta(minutes=5),
    )
    assert ledger.delivery_by_handle("hdl-old").state is SecretDeliveryState.REVOKED
    assert ledger.delivery_by_handle("hdl-new").state is SecretDeliveryState.ACTIVE


def test_expire_due_marks_active_deliveries_expired() -> None:
    ledger, delivery = _issue(_ledger(), ttl=timedelta(minutes=5))
    ledger = ledger.expire_due(observed_at=T0 + timedelta(minutes=20))
    assert ledger.delivery_by_handle(delivery.delivery_handle).state is SecretDeliveryState.EXPIRED
    with pytest.raises(DomainValidationError) as caught:
        ledger.authorize_use(
            delivery_handle=delivery.delivery_handle,
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at=T0 + timedelta(minutes=21),
        )
    assert caught.value.reason == "expired"


# ── Log redaction ────────────────────────────────────────────────────────────


def test_redact_secret_material_scrubs_handles_and_literals() -> None:
    text = (
        "issued handle=hdl-001 token=super-secret-value "
        "url=https://x/?token=abc&ok=1 Authorization: Bearer xyz"
    )
    scrubbed = redact_secret_material(
        text,
        handles=("hdl-001",),
        extra_literals=("super-secret-value", "xyz"),
    )
    assert "hdl-001" not in scrubbed
    assert "super-secret-value" not in scrubbed
    assert "xyz" not in scrubbed
    assert "REDACTED" in scrubbed
    assert "ok=1" in scrubbed


def test_redact_rejects_non_string_text() -> None:
    with pytest.raises(DomainValidationError):
        redact_secret_material(None)  # type: ignore[arg-type]


def test_issue_rejects_blank_binding_fields() -> None:
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), purpose=" ")
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), target_id="")
    with pytest.raises(DomainValidationError):
        _issue(_ledger(), secret_ref=" ")


def test_delivery_rejects_invalid_construction() -> None:
    from qarunner.domain import SecretDelivery

    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=SecretDeliveryState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=0,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=SecretDeliveryState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=object(),  # type: ignore[arg-type]
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=SecretDeliveryState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at=T0,
            state=SecretDeliveryState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state="active",  # type: ignore[arg-type]
        )
    naive = datetime(2026, 7, 28, 20, 0)
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=naive,
            expires_at=T0 + timedelta(minutes=1),
            state=SecretDeliveryState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        SecretDelivery(
            delivery_handle="h",
            secret_ref="secret://x",
            secret_version="v1",
            run_id="run-001",
            attempt_id="attempt-001",
            fence=1,
            worker=WORKER,
            target_id="t",
            purpose="p",
            issued_at=T0,
            expires_at="later",  # type: ignore[arg-type]
            state=SecretDeliveryState.ACTIVE,
        )


def test_ledger_rejects_invalid_rehydrate_and_clocks() -> None:
    from qarunner.domain import SecretDelivery

    with pytest.raises(DomainValidationError):
        SecretDeliveryLedger(deliveries="bad")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        SecretDeliveryLedger(deliveries=(object(),))  # type: ignore[arg-type]

    good = SecretDelivery(
        delivery_handle="h1",
        secret_ref="secret://x",
        secret_version="v1",
        run_id="run-001",
        attempt_id="attempt-001",
        fence=1,
        worker=WORKER,
        target_id="t",
        purpose="p",
        issued_at=T0,
        expires_at=T0 + timedelta(minutes=5),
        state=SecretDeliveryState.ACTIVE,
    )
    with pytest.raises(DomainValidationError):
        SecretDeliveryLedger(deliveries=(good, good))

    ledger, delivery = _issue(_ledger())
    with pytest.raises(DomainValidationError):
        ledger.authorize_use(
            delivery_handle=delivery.delivery_handle,
            attempt_id="attempt-001",
            fence=1,
            target_id="target-staging-web",
            purpose="browser-login",
            observed_at="now",  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        ledger.revoke(delivery_handle=delivery.delivery_handle, revoked_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        ledger.revoke(delivery_handle="missing", revoked_at=T0 + timedelta(minutes=1))
    with pytest.raises(DomainValidationError):
        ledger.revoke_all_for_attempt(
            attempt_id=" ",
            fence=1,
            revoked_at=T0 + timedelta(minutes=1),
        )
    with pytest.raises(DomainValidationError):
        ledger.revoke_all_for_attempt(
            attempt_id="attempt-001",
            fence=0,
            revoked_at=T0 + timedelta(minutes=1),
        )
    with pytest.raises(DomainValidationError):
        ledger.expire_due(observed_at=datetime(2026, 7, 28, 20, 0))


def test_revoke_non_active_is_rejected_except_idempotent_revoked() -> None:
    ledger, delivery = _issue(_ledger(), ttl=timedelta(minutes=5))
    ledger = ledger.expire_due(observed_at=T0 + timedelta(minutes=20))
    with pytest.raises(DomainValidationError) as caught:
        ledger.revoke(
            delivery_handle=delivery.delivery_handle,
            revoked_at=T0 + timedelta(minutes=21),
        )
    assert caught.value.reason == "not_active"


def test_is_active_at_and_redact_edge_cases() -> None:
    ledger, delivery = _issue(_ledger())
    assert delivery.is_active_at(T0 + timedelta(minutes=2)) is True
    assert delivery.is_active_at(T0 + timedelta(minutes=20)) is False
    with pytest.raises(DomainValidationError):
        delivery.is_active_at("now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        delivery.is_active_at(datetime(2026, 7, 28, 20, 0))

    # Revoked delivery is never active even inside the original TTL window.
    ledger = ledger.revoke(
        delivery_handle=delivery.delivery_handle,
        revoked_at=T0 + timedelta(minutes=2),
    )
    revoked = ledger.delivery_by_handle(delivery.delivery_handle)
    assert revoked is not None
    assert revoked.is_active_at(T0 + timedelta(minutes=3)) is False

    with pytest.raises(DomainValidationError):
        redact_secret_material(None)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        redact_secret_material("x", handles="h")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        redact_secret_material("x", extra_literals="s")  # type: ignore[arg-type]
    assert redact_secret_material("plain") == "plain"
    assert redact_secret_material("handle=h1", handles=("", "h1")) == "handle=[REDACTED]"
