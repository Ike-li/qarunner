"""T-M5-LEASE-001 foundation: Environment Lease under TargetGrant ceiling.

Grant is the permission ceiling; Lease is one Attempt's occupancy.
Concurrent acquires must not oversell ``max_concurrent_units``. TTL expiry
and explicit release free units; Grant revoke/suspend blocks new acquires
and marks active leases non-effective for egress.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    DomainValidationError,
    EnvironmentLeaseBook,
    EnvironmentLeaseState,
    LeaseDimension,
    TargetGrant,
    TargetGrantState,
    TargetSpec,
)

T0 = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)


def _spec(**changes) -> TargetSpec:
    values = {
        "target_id": "target-staging-web",
        "environment": "staging",
        "allowed_dns_names": ("app.staging.example.com",),
        "allowed_protocols": ("https",),
        "allowed_ports": (443,),
        "allow_private_rfc1918": False,
        "owner_id": "owner-001",
    }
    values.update(changes)
    return TargetSpec(**values)


def _grant(*, max_units: int = 3, **changes) -> TargetGrant:
    values = {
        "grant_id": "grant-001",
        "target": _spec(),
        "state": TargetGrantState.ACTIVE,
        "max_concurrent_units": max_units,
        "valid_from": T0,
        "valid_until": T0 + timedelta(hours=4),
        "version": 1,
    }
    values.update(changes)
    return TargetGrant(**values)


def _book(grant: TargetGrant | None = None) -> EnvironmentLeaseBook:
    return EnvironmentLeaseBook.empty(grant=grant if grant is not None else _grant())


# ── Acquire under ceiling ────────────────────────────────────────────────────


def test_acquire_reserves_units_under_grant_ceiling() -> None:
    book = _book(_grant(max_units=3))
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    assert lease.state is EnvironmentLeaseState.ACTIVE
    assert lease.units == 2
    assert lease.grant_id == "grant-001"
    assert lease.target_id == "target-staging-web"
    assert lease.expires_at == T0 + timedelta(minutes=31)
    assert book.active_units_at(T0 + timedelta(minutes=2)) == 2


def test_concurrent_acquires_cannot_oversell_grant_ceiling() -> None:
    """T-M5-LEASE-001: multi-Attempt races must not exceed max_concurrent_units."""
    book = _book(_grant(max_units=3))
    book, _ = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    with pytest.raises(DomainValidationError) as caught:
        book.acquire(
            lease_id="lease-b",
            attempt_id="attempt-002",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=2,  # 2+2 > 3
            acquired_at=T0 + timedelta(minutes=2),
            ttl=timedelta(minutes=30),
        )
    assert caught.value.field == "units"
    assert caught.value.reason == "oversell"
    # Book unchanged after rejected acquire.
    assert book.active_units_at(T0 + timedelta(minutes=2)) == 2


def test_exact_ceiling_fill_is_allowed_then_blocks_next() -> None:
    book = _book(_grant(max_units=2))
    book, _ = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    assert book.active_units_at(T0 + timedelta(minutes=1)) == 2
    with pytest.raises(DomainValidationError) as caught:
        book.acquire(
            lease_id="lease-b",
            attempt_id="attempt-002",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=2),
            ttl=timedelta(minutes=30),
        )
    assert caught.value.reason == "oversell"


def test_acquire_rejected_when_grant_not_effective() -> None:
    book = _book(_grant(state=TargetGrantState.REVOKED))
    with pytest.raises(DomainValidationError) as caught:
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )
    assert caught.value.reason == "grant_not_effective"


def test_duplicate_lease_id_is_rejected() -> None:
    book = _book()
    book, _ = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    with pytest.raises(DomainValidationError) as caught:
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-002",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=2),
            ttl=timedelta(minutes=10),
        )
    assert caught.value.field == "lease_id"
    assert caught.value.reason == "duplicate"


# ── Release / TTL expiry frees capacity ──────────────────────────────────────


def test_release_frees_units_for_subsequent_acquire() -> None:
    book = _book(_grant(max_units=2))
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    book = book.release(lease_id=lease.lease_id, released_at=T0 + timedelta(minutes=5))
    assert book.active_units_at(T0 + timedelta(minutes=5)) == 0
    book, lease_b = book.acquire(
        lease_id="lease-b",
        attempt_id="attempt-002",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=6),
        ttl=timedelta(minutes=30),
    )
    assert lease_b.units == 2
    assert book.active_units_at(T0 + timedelta(minutes=6)) == 2


def test_ttl_expiry_frees_units_without_explicit_release() -> None:
    book = _book(_grant(max_units=2))
    book, _ = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    # Still active just before expiry.
    assert book.active_units_at(T0 + timedelta(minutes=10, seconds=59)) == 2
    # At/after expiry the units no longer count toward the ceiling.
    assert book.active_units_at(T0 + timedelta(minutes=11, seconds=1)) == 0
    book, lease_b = book.acquire(
        lease_id="lease-b",
        attempt_id="attempt-002",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=12),
        ttl=timedelta(minutes=10),
    )
    assert lease_b.units == 2


def test_expire_due_marks_active_leases_expired() -> None:
    book = _book(_grant(max_units=2))
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=5),
    )
    book = book.expire_due(observed_at=T0 + timedelta(minutes=10))
    expired = book.lease_by_id(lease.lease_id)
    assert expired is not None
    assert expired.state is EnvironmentLeaseState.EXPIRED
    assert book.active_units_at(T0 + timedelta(minutes=10)) == 0


# ── Revoke / Grant suspend ───────────────────────────────────────────────────


def test_revoke_lease_makes_it_non_effective_and_frees_units() -> None:
    book = _book(_grant(max_units=2))
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    book = book.revoke(lease_id=lease.lease_id, revoked_at=T0 + timedelta(minutes=3))
    revoked = book.lease_by_id(lease.lease_id)
    assert revoked is not None
    assert revoked.state is EnvironmentLeaseState.REVOKED
    assert book.active_units_at(T0 + timedelta(minutes=3)) == 0
    assert revoked.is_effective_at(T0 + timedelta(minutes=3)) is False


def test_revoke_all_for_grant_suspend_invalidates_every_active_lease() -> None:
    """Grant suspend/revoke must make activity leases non-effective for egress."""
    book = _book(_grant(max_units=5))
    book, _ = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=30),
    )
    book, _ = book.acquire(
        lease_id="lease-b",
        attempt_id="attempt-002",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=2,
        acquired_at=T0 + timedelta(minutes=2),
        ttl=timedelta(minutes=30),
    )
    book = book.revoke_all_for_grant(revoked_at=T0 + timedelta(minutes=5))
    assert book.active_units_at(T0 + timedelta(minutes=5)) == 0
    assert all(
        lease.state is EnvironmentLeaseState.REVOKED
        for lease in book.leases
        if lease.lease_id in {"lease-a", "lease-b"}
    )


# ── Identity / validation ────────────────────────────────────────────────────


def test_lease_binds_attempt_fence_grant_and_dimension() -> None:
    book = _book()
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=3,
        dimension=LeaseDimension.ACCOUNT,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=15),
    )
    assert lease.attempt_id == "attempt-001"
    assert lease.fence == 3
    assert lease.dimension is LeaseDimension.ACCOUNT
    assert lease.grant_id == book.grant.grant_id


def test_acquire_rejects_invalid_units_and_ttl() -> None:
    book = _book()
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=0,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(0),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=0,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )


def test_release_unknown_lease_is_rejected() -> None:
    book = _book()
    with pytest.raises(DomainValidationError) as caught:
        book.release(lease_id="missing", released_at=T0 + timedelta(minutes=1))
    assert caught.value.field == "lease_id"
    assert caught.value.reason == "not_found"


def test_double_release_is_idempotent_replay() -> None:
    book = _book()
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    book = book.release(lease_id=lease.lease_id, released_at=T0 + timedelta(minutes=2))
    again = book.release(lease_id=lease.lease_id, released_at=T0 + timedelta(minutes=3))
    assert again.lease_by_id(lease.lease_id).state is EnvironmentLeaseState.RELEASED
    assert again.active_units_at(T0 + timedelta(minutes=3)) == 0


def test_lease_and_book_reject_invalid_construction() -> None:
    from qarunner.domain import EnvironmentLease

    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=0,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension="session",  # type: ignore[arg-type]
            units=1,
            acquired_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=0,
            acquired_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at=T0,
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLeaseBook.empty(grant=object())  # type: ignore[arg-type]


def test_book_rejects_cross_grant_or_duplicate_member_on_rehydrate() -> None:
    from qarunner.domain import EnvironmentLease

    grant = _grant()
    lease = EnvironmentLease(
        lease_id="lease-a",
        grant_id="other-grant",
        target_id=grant.target.target_id,
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0,
        expires_at=T0 + timedelta(minutes=5),
        state=EnvironmentLeaseState.ACTIVE,
    )
    with pytest.raises(DomainValidationError) as caught:
        EnvironmentLeaseBook(grant=grant, leases=(lease,))
    assert caught.value.reason == "grant_mismatch"

    good = EnvironmentLease(
        lease_id="lease-a",
        grant_id=grant.grant_id,
        target_id=grant.target.target_id,
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0,
        expires_at=T0 + timedelta(minutes=5),
        state=EnvironmentLeaseState.ACTIVE,
    )
    with pytest.raises(DomainValidationError) as dup:
        EnvironmentLeaseBook(grant=grant, leases=(good, good))
    assert dup.value.reason == "duplicate_id"

    wrong_target = EnvironmentLease(
        lease_id="lease-b",
        grant_id=grant.grant_id,
        target_id="other-target",
        attempt_id="attempt-002",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0,
        expires_at=T0 + timedelta(minutes=5),
        state=EnvironmentLeaseState.ACTIVE,
    )
    with pytest.raises(DomainValidationError) as tgt:
        EnvironmentLeaseBook(grant=grant, leases=(wrong_target,))
    assert tgt.value.reason == "target_mismatch"


def test_revoke_unknown_or_non_active_paths() -> None:
    book = _book()
    with pytest.raises(DomainValidationError):
        book.revoke(lease_id="missing", revoked_at=T0 + timedelta(minutes=1))
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    book = book.release(lease_id=lease.lease_id, released_at=T0 + timedelta(minutes=2))
    with pytest.raises(DomainValidationError) as caught:
        book.revoke(lease_id=lease.lease_id, revoked_at=T0 + timedelta(minutes=3))
    assert caught.value.reason == "not_active"


def test_double_revoke_is_idempotent() -> None:
    book = _book()
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    book = book.revoke(lease_id=lease.lease_id, revoked_at=T0 + timedelta(minutes=2))
    again = book.revoke(lease_id=lease.lease_id, revoked_at=T0 + timedelta(minutes=3))
    assert again.lease_by_id(lease.lease_id).state is EnvironmentLeaseState.REVOKED


def test_lease_is_effective_rejects_bad_clock() -> None:
    book = _book()
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    with pytest.raises(DomainValidationError):
        lease.is_effective_at("now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        lease.is_effective_at(datetime(2026, 7, 28, 18, 0))


def test_acquire_rejects_untyped_ids_and_dimension() -> None:
    book = _book()
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id=" ",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension="session",  # type: ignore[arg-type]
            units=1,
            acquired_at=T0 + timedelta(minutes=1),
            ttl=timedelta(minutes=10),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at="now",  # type: ignore[arg-type]
            ttl=timedelta(minutes=10),
        )
    with pytest.raises(DomainValidationError):
        book.acquire(
            lease_id="lease-a",
            attempt_id="attempt-001",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=datetime(2026, 7, 28, 18, 0),
            ttl=timedelta(minutes=10),
        )


def test_release_revoke_expire_reject_bad_clock_and_not_active_release() -> None:
    book = _book()
    book, lease = book.acquire(
        lease_id="lease-a",
        attempt_id="attempt-001",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=10),
    )
    with pytest.raises(DomainValidationError):
        book.release(lease_id=lease.lease_id, released_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        book.release(lease_id=lease.lease_id, released_at=datetime(2026, 7, 28, 18, 0))
    with pytest.raises(DomainValidationError):
        book.revoke(lease_id=lease.lease_id, revoked_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        book.revoke(lease_id=lease.lease_id, revoked_at=datetime(2026, 7, 28, 18, 0))
    with pytest.raises(DomainValidationError):
        book.revoke_all_for_grant(revoked_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        book.revoke_all_for_grant(revoked_at=datetime(2026, 7, 28, 18, 0))
    with pytest.raises(DomainValidationError):
        book.expire_due(observed_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        book.expire_due(observed_at=datetime(2026, 7, 28, 18, 0))

    # Release then try release again is idempotent; revoke path already covered.
    # Expire then release must fail not_active.
    book2 = _book()
    book2, lease2 = book2.acquire(
        lease_id="lease-b",
        attempt_id="attempt-002",
        fence=1,
        dimension=LeaseDimension.SESSION,
        units=1,
        acquired_at=T0 + timedelta(minutes=1),
        ttl=timedelta(minutes=5),
    )
    book2 = book2.expire_due(observed_at=T0 + timedelta(minutes=10))
    with pytest.raises(DomainValidationError) as caught:
        book2.release(lease_id=lease2.lease_id, released_at=T0 + timedelta(minutes=11))
    assert caught.value.reason == "not_active"


def test_lease_rejects_naive_datetimes_and_bad_state() -> None:
    from qarunner.domain import EnvironmentLease

    naive = datetime(2026, 7, 28, 18, 0)
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=naive,
            expires_at=T0 + timedelta(minutes=1),
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at=naive,
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at="later",  # type: ignore[arg-type]
            state=EnvironmentLeaseState.ACTIVE,
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLease(
            lease_id="l",
            grant_id="g",
            target_id="t",
            attempt_id="a",
            fence=1,
            dimension=LeaseDimension.SESSION,
            units=1,
            acquired_at=T0,
            expires_at=T0 + timedelta(minutes=1),
            state="active",  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        EnvironmentLeaseBook(grant=_grant(), leases="bad")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        EnvironmentLeaseBook(grant=_grant(), leases=(object(),))  # type: ignore[arg-type]
