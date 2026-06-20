"""Tests for UuidIds adapter."""

from __future__ import annotations

import uuid

from qarunner.adapters.uuid_ids import UuidIds


def test_returns_valid_uuid_string() -> None:
    gen = UuidIds()
    result = gen.new_id()
    # Should parse as a valid UUID
    parsed = uuid.UUID(result)
    assert str(parsed) == result


def test_successive_calls_differ() -> None:
    gen = UuidIds()
    ids = {gen.new_id() for _ in range(100)}
    assert len(ids) == 100
