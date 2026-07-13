"""T-M0-PORT-001 clock contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from tests.fakes.greenfield.clock import FakeUtcClock

from qarunner.application.ports.clock import UtcClock
from qarunner.application.ports.common import PortContractError


def test_fake_clock_replays_an_injected_utc_instant() -> None:
    fixed = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    clock = FakeUtcClock(fixed)

    assert isinstance(clock, UtcClock)
    assert clock.now() == fixed
    assert clock.now() == fixed


@pytest.mark.parametrize(
    "invalid",
    [
        datetime(2026, 7, 13, 12, 0),
        datetime(2026, 7, 13, 12, 0, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_fake_clock_rejects_non_utc_initial_value(invalid: datetime) -> None:
    with pytest.raises(PortContractError) as captured:
        FakeUtcClock(invalid)

    assert captured.value.code == "application_port/contract_error"
    assert captured.value.resource == "clock"
    assert captured.value.field == "current"
    assert captured.value.reason == "not_utc"


def test_fake_clock_rejects_a_non_datetime_initial_value() -> None:
    with pytest.raises(PortContractError) as captured:
        FakeUtcClock("2026-07-13T12:00:00Z")  # type: ignore[arg-type]

    assert captured.value.resource == "clock"
    assert captured.value.field == "current"
    assert captured.value.reason == "not_datetime"


def test_fake_clock_changes_only_via_explicit_set_and_advance() -> None:
    initial = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    rewound = datetime(2026, 7, 13, 11, 0, tzinfo=UTC)
    clock = FakeUtcClock(initial)

    clock.set(rewound)
    assert clock.now() == rewound

    clock.advance(timedelta(seconds=30))
    assert clock.now() == rewound + timedelta(seconds=30)
