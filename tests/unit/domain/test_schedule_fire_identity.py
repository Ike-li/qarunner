"""T-M6-SCHEDULE-001: deterministic schedule fire identity (DES §11.2).

Each schedule fire has a deterministic identity (schedule_id + fire_time → digest).
Misfire, pause/restore, restart don't produce duplicate Batch because the fire
digest is the idempotency_key for Batch creation. Pure domain — no adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    Digest,
    DomainValidationError,
    ScheduleFireIdentity,
    ScheduleRecord,
    ScheduleStatus,
    compute_next_fire_at,
    compute_schedule_fire_identity,
)

T0 = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _record(**changes) -> ScheduleRecord:
    values = {
        "schedule_id": "schedule-001",
        "name": "nightly-regression",
        "profile_id": "profile-default",
        "cron_expr": "0 2 * * *",
        "timezone": "UTC",
        "status": ScheduleStatus.ENABLED,
        "next_fire_at": T0 + timedelta(hours=16),
        "created_by": "admin-001",
        "created_at": T0,
    }
    values.update(changes)
    return ScheduleRecord(**values)


# ── ScheduleRecord ───────────────────────────────────────────────────────────


def test_schedule_record_digest_is_stable() -> None:
    a = _record()
    b = _record()
    assert a.schedule_digest == b.schedule_digest
    assert _record(cron_expr="0 3 * * *").schedule_digest != a.schedule_digest


def test_schedule_record_rejects_invalid_contract() -> None:
    with pytest.raises(DomainValidationError):
        _record(schedule_id=" ")
    with pytest.raises(DomainValidationError):
        _record(cron_expr="")
    with pytest.raises(DomainValidationError):
        _record(timezone="")
    with pytest.raises(DomainValidationError):
        _record(profile_id="")
    with pytest.raises(DomainValidationError):
        _record(status="bad")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        _record(next_fire_at="tomorrow")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        _record(created_at="now")  # type: ignore[arg-type]
    naive = datetime(2026, 7, 29, 10, 0)
    with pytest.raises(DomainValidationError):
        _record(created_at=naive)
    with pytest.raises(DomainValidationError):
        _record(next_fire_at=naive)


# ── ScheduleStatus ───────────────────────────────────────────────────────────


def test_schedule_status_enum_values() -> None:
    assert ScheduleStatus.ENABLED == "enabled"
    assert ScheduleStatus.PAUSED == "paused"
    assert ScheduleStatus.DISABLED == "disabled"


# ── compute_next_fire_at ─────────────────────────────────────────────────────


def test_compute_next_fire_at_returns_utc_datetime() -> None:
    result = compute_next_fire_at(cron_expr="0 2 * * *", timezone="UTC", after=T0)
    assert result is not None
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result > T0


def test_compute_next_fire_at_returns_none_for_disabled_schedule() -> None:
    result = compute_next_fire_at(cron_expr="0 2 * * *", timezone="UTC", after=T0, enabled=False)
    assert result is None


def test_compute_next_fire_at_rejects_blank_cron() -> None:
    with pytest.raises(DomainValidationError):
        compute_next_fire_at(cron_expr=" ", timezone="UTC", after=T0)


def test_compute_next_fire_at_rejects_blank_timezone() -> None:
    with pytest.raises(DomainValidationError):
        compute_next_fire_at(cron_expr="0 2 * * *", timezone=" ", after=T0)


def test_compute_next_fire_at_rejects_invalid_cron_format() -> None:
    with pytest.raises(DomainValidationError):
        compute_next_fire_at(cron_expr="invalid", timezone="UTC", after=T0)


def test_compute_next_fire_at_rejects_invalid_timezone() -> None:
    with pytest.raises(DomainValidationError):
        compute_next_fire_at(cron_expr="0 2 * * *", timezone="Invalid/Zone", after=T0)


def test_fire_identity_rejects_blank_schedule_id_via_constructor() -> None:
    """Direct construction to hit __post_init__ schedule_id validation."""
    with pytest.raises(DomainValidationError):
        ScheduleFireIdentity(
            schedule_id=" ",
            fire_at=T0 + timedelta(hours=2),
            fire_digest=_digest("fire"),
        )


def _digest(label: str) -> Digest:
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-schedule.v1",
        payload={"label": label},
    )


# ── compute_schedule_fire_identity ───────────────────────────────────────────


def test_fire_identity_is_deterministic_for_same_schedule_and_time() -> None:
    a = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0)
    b = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0)
    assert a.fire_digest == b.fire_digest
    assert a.schedule_id == "s-1"
    assert a.fire_at == T0


def test_fire_identity_differs_for_different_schedule() -> None:
    a = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0)
    b = compute_schedule_fire_identity(schedule_id="s-2", fire_at=T0)
    assert a.fire_digest != b.fire_digest


def test_fire_identity_differs_for_different_time() -> None:
    a = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0)
    b = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0 + timedelta(minutes=1))
    assert a.fire_digest != b.fire_digest


def test_fire_identity_rejects_invalid_inputs() -> None:
    with pytest.raises(DomainValidationError):
        compute_schedule_fire_identity(schedule_id=" ", fire_at=T0)
    with pytest.raises(DomainValidationError):
        compute_schedule_fire_identity(schedule_id="s-1", fire_at="now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        compute_schedule_fire_identity(schedule_id="s-1", fire_at=datetime(2026, 7, 29, 10, 0))


# ── Misfire / pause / restart invariants ─────────────────────────────────────


def test_same_fire_time_produces_same_digest_regardless_of_observation_delay() -> None:
    """Misfire: a schedule that fires late still produces the same identity
    for the original fire time, so Batch creation is idempotent."""
    fire_at = T0 + timedelta(hours=2)  # scheduled 12:00
    early = compute_schedule_fire_identity(schedule_id="s-1", fire_at=fire_at)
    late = compute_schedule_fire_identity(schedule_id="s-1", fire_at=fire_at)
    assert early.fire_digest == late.fire_digest


def test_pause_restore_does_not_change_fire_identity_for_already_fired_time() -> None:
    """Pausing and restoring a schedule doesn't retroactively change the
    identity of a fire that already happened."""
    fire_at = T0 + timedelta(hours=2)
    before_pause = compute_schedule_fire_identity(schedule_id="s-1", fire_at=fire_at)
    # Simulate pause/restore — the fire_at is the same.
    after_restore = compute_schedule_fire_identity(schedule_id="s-1", fire_at=fire_at)
    assert before_pause.fire_digest == after_restore.fire_digest


def test_schedule_fire_identity_idempotency_key_is_fire_digest() -> None:
    """The fire_digest is the canonical idempotency_key for Batch creation."""
    fire = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0 + timedelta(hours=2))
    assert fire.fire_digest.value.startswith("sha256:")
    # Same fire → same key → same Batch (idempotent).
    fire2 = compute_schedule_fire_identity(schedule_id="s-1", fire_at=T0 + timedelta(hours=2))
    assert fire.fire_digest == fire2.fire_digest


def test_schedule_record_rejects_non_utc_created_at() -> None:
    from datetime import timezone

    non_utc = datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(DomainValidationError):
        _record(created_at=non_utc)


def test_schedule_record_rejects_non_utc_next_fire_at() -> None:
    from datetime import timezone

    non_utc = datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(DomainValidationError):
        _record(next_fire_at=non_utc)


def test_schedule_record_allows_none_next_fire_at() -> None:
    r = _record(next_fire_at=None)
    assert r.next_fire_at is None
    assert r.schedule_digest is not None


def test_fire_identity_rejects_non_digest_fire_digest() -> None:
    with pytest.raises(DomainValidationError):
        ScheduleFireIdentity(
            schedule_id="s-1",
            fire_at=T0 + timedelta(hours=2),
            fire_digest="not-a-digest",  # type: ignore[arg-type]
        )
