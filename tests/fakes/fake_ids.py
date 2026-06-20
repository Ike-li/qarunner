"""Fake ID generator for testing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeIdGenerator:
    """Generates sequential IDs: id-001, id-002, …"""

    _counter: int = 0

    def new_id(self) -> str:
        self._counter += 1
        return f"id-{self._counter:03d}"
