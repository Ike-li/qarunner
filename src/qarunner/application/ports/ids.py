"""Opaque entity-ID generation port."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class OpaqueIdGenerator(Protocol):
    """Return one opaque 128-bit identifier supplied by an adapter."""

    def new_id(self) -> str: ...
