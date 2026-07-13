"""T-M0-PORT-001 opaque secret-broker contract."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.greenfield.clock import FakeUtcClock
from tests.fakes.greenfield.secrets import DenyAllSecretBroker, PresetSecretBroker

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.secrets import (
    SecretBroker,
    SecretDeliveryLease,
    SecretRequest,
)
from qarunner.domain import AttemptAuthority, WorkerRef


async def test_secret_broker_fails_closed_without_an_explicit_grant() -> None:
    request = _request()
    broker = DenyAllSecretBroker()

    assert isinstance(broker, SecretBroker)
    with pytest.raises(PortContractError) as captured:
        await broker.issue(request)

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "request"
    assert captured.value.reason == "not_authorized"


async def test_secret_broker_issues_only_an_opaque_preset_delivery_lease() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request()
    lease = SecretDeliveryLease(
        delivery_handle="secret-delivery-018f8f325a667c10",
        expires_at=now + timedelta(minutes=5),
    )
    broker = PresetSecretBroker(
        FakeUtcClock(now),
        ((request, lease),),
    )

    issued = await broker.issue(request)

    assert issued == lease
    assert tuple(field.name for field in fields(issued)) == (
        "delivery_handle",
        "expires_at",
    )
    assert broker.issued_requests() == (request,)


@pytest.mark.parametrize(
    "changes",
    [
        {"secret_ref": "secret://sut/account-2"},
        {"secret_version": "version-8"},
        {"run_id": "run-2"},
        {"attempt_id": "attempt-2"},
        {
            "authority": AttemptAuthority(
                current_fence=4,
                current_worker=WorkerRef(worker_id="worker-1", generation=2),
            )
        },
        {
            "authority": AttemptAuthority(
                current_fence=3,
                current_worker=WorkerRef(worker_id="worker-1", generation=3),
            )
        },
        {"target_id": "target-2"},
        {"purpose": "api-login"},
        {"requested_expires_at": datetime(2026, 7, 13, 13, 1, tzinfo=UTC)},
    ],
)
async def test_secret_broker_rejects_every_unseeded_binding_change(
    changes: dict[str, object],
) -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request()
    lease = SecretDeliveryLease(
        delivery_handle="secret-delivery-018f8f325a667c10",
        expires_at=now + timedelta(minutes=5),
    )
    broker = PresetSecretBroker(FakeUtcClock(now), ((request, lease),))

    with pytest.raises(PortContractError) as captured:
        await broker.issue(replace(request, **changes))

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "request"
    assert captured.value.reason == "not_authorized"
    assert broker.issued_requests() == ()


async def test_secret_broker_rejects_a_lease_at_its_expiry_boundary() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request()
    broker = PresetSecretBroker(
        FakeUtcClock(now),
        (
            (
                request,
                SecretDeliveryLease(
                    delivery_handle="secret-delivery-018f8f325a667c10",
                    expires_at=now,
                ),
            ),
        ),
    )

    with pytest.raises(PortContractError) as captured:
        await broker.issue(request)

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "lease"
    assert captured.value.reason == "expired"
    assert broker.issued_requests() == ()


async def test_secret_broker_does_not_reissue_a_revoked_delivery_handle() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request()
    lease = SecretDeliveryLease(
        delivery_handle="secret-delivery-018f8f325a667c10",
        expires_at=now + timedelta(minutes=5),
    )
    broker = PresetSecretBroker(
        FakeUtcClock(now),
        ((request, lease),),
    )
    await broker.issue(request)

    await broker.revoke(lease.delivery_handle)
    await broker.revoke(lease.delivery_handle)

    with pytest.raises(PortContractError) as captured:
        await broker.issue(request)

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "lease"
    assert captured.value.reason == "revoked"
    assert broker.issued_requests() == (request,)


async def test_secret_broker_rejects_revocation_of_an_unknown_handle() -> None:
    broker = PresetSecretBroker(
        FakeUtcClock(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)),
        (),
    )

    with pytest.raises(PortContractError) as captured:
        await broker.revoke("secret-delivery-unknown")

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "delivery_handle"
    assert captured.value.reason == "unknown"


async def test_deny_all_secret_broker_rejects_revocation() -> None:
    broker = DenyAllSecretBroker()

    with pytest.raises(PortContractError) as captured:
        await broker.revoke("secret-delivery-018f8f325a667c10")

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "delivery_handle"
    assert captured.value.reason == "not_authorized"


async def test_secret_broker_rejects_a_lease_beyond_the_requested_expiry() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request(requested_expires_at=now + timedelta(minutes=5))
    broker = PresetSecretBroker(
        FakeUtcClock(now),
        (
            (
                request,
                SecretDeliveryLease(
                    delivery_handle="secret-delivery-018f8f325a667c10",
                    expires_at=now + timedelta(minutes=10),
                ),
            ),
        ),
    )

    with pytest.raises(PortContractError) as captured:
        await broker.issue(request)

    assert captured.value.resource == "secret_broker"
    assert captured.value.field == "lease"
    assert captured.value.reason == "exceeds_requested_expiry"
    assert broker.issued_requests() == ()


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"secret_ref": ""}, "secret_ref", "empty"),
        ({"secret_version": "   "}, "secret_version", "empty"),
        ({"purpose": 7}, "purpose", "not_string"),
        ({"authority": None}, "authority", "not_attempt_authority"),
        (
            {"requested_expires_at": datetime(2026, 7, 13, 12, 5)},
            "requested_expires_at",
            "not_utc",
        ),
    ],
)
def test_secret_request_rejects_invalid_binding_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    with pytest.raises(PortContractError) as captured:
        replace(_request(), **changes)

    assert captured.value.resource == "secret_request"
    assert captured.value.field == field
    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"delivery_handle": ""}, "delivery_handle", "empty"),
        ({"delivery_handle": None}, "delivery_handle", "not_string"),
        (
            {"expires_at": datetime(2026, 7, 13, 12, 5)},
            "expires_at",
            "not_utc",
        ),
    ],
)
def test_secret_delivery_lease_rejects_invalid_opaque_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    lease = SecretDeliveryLease(
        delivery_handle="secret-delivery-018f8f325a667c10",
        expires_at=datetime(2026, 7, 13, 12, 5, tzinfo=UTC),
    )

    with pytest.raises(PortContractError) as captured:
        replace(lease, **changes)

    assert captured.value.resource == "secret_delivery_lease"
    assert captured.value.field == field
    assert captured.value.reason == reason


def _request(*, requested_expires_at: datetime | None = None) -> SecretRequest:
    return SecretRequest(
        secret_ref="secret://sut/account-1",
        secret_version="version-7",
        run_id="run-1",
        attempt_id="attempt-1",
        authority=AttemptAuthority(
            current_fence=3,
            current_worker=WorkerRef(worker_id="worker-1", generation=2),
        ),
        target_id="target-1",
        purpose="browser-login",
        requested_expires_at=(
            datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
            if requested_expires_at is None
            else requested_expires_at
        ),
    )
