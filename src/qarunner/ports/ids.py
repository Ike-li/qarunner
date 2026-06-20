"""ID generation port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdGenerator(Protocol):
    """Port for generating unique identifiers."""

    def new_id(self) -> str: ...
