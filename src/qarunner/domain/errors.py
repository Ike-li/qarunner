"""Stable errors exposed by the greenfield execution domain."""

from __future__ import annotations

import enum


class InvalidTransition(ValueError):
    """Raised when a domain aggregate rejects a requested state edge."""

    code = "invalid_transition"

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_state: enum.StrEnum,
        requested_state: enum.StrEnum,
        current_version: int,
        expected_version: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_state = current_state
        self.requested_state = requested_state
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"{entity_type} {entity_id} cannot transition "
            f"from {current_state} to {requested_state}"
        )


class VersionConflict(ValueError):
    """Raised when a command's expected aggregate version is stale."""

    code = "version_conflict"

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_version: int,
        expected_version: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"{entity_type} {entity_id} has version {current_version}; expected {expected_version}"
        )


def ensure_expected_version(
    *, entity_type: str, entity_id: str, current_version: int, expected_version: int
) -> None:
    """Apply the shared CAS precondition for immutable domain commands."""
    if expected_version != current_version:
        raise VersionConflict(
            entity_type=entity_type,
            entity_id=entity_id,
            current_version=current_version,
            expected_version=expected_version,
        )
