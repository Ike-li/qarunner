"""Cron expression helpers — the single source of truth for validating cron
expressions / timezones and computing next/previous run times.

Both the API routes (preview, create/update schedule) and the schedule adapter
go through these helpers, so a previewed next-run is computed the same way as
the persisted one.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime

from croniter import croniter


def is_valid_timezone(tz: str) -> bool:
    """Return whether *tz* is a known IANA timezone name."""
    try:
        zoneinfo.ZoneInfo(tz)
    except Exception:
        return False
    return True


def is_valid_cron(expression: str) -> bool:
    """Return whether *expression* is a valid cron expression."""
    return croniter.is_valid(expression)


def next_runs(expression: str, tz: str, count: int = 1) -> list[datetime]:
    """Return the next *count* fire times of *expression* in timezone *tz*.

    Raises if the expression or timezone is invalid; callers needing a boolean
    check should use :func:`is_valid_cron` / :func:`is_valid_timezone` first.
    """
    zone = zoneinfo.ZoneInfo(tz)
    it = croniter(expression, datetime.now(zone))
    return [it.get_next(datetime) for _ in range(count)]


def next_run(expression: str, tz: str) -> datetime:
    """Return the single next fire time of *expression* in timezone *tz*."""
    return next_runs(expression, tz, 1)[0]


def previous_run(expression: str, tz: str) -> datetime:
    """Return the most recent past fire time (the current cron tick)."""
    zone = zoneinfo.ZoneInfo(tz)
    return croniter(expression, datetime.now(zone)).get_prev(datetime)
