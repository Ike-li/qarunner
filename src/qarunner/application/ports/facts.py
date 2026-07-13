"""Typed, versioned fact-store port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain.event import AttemptEvent
from qarunner.domain.idempotency import IdempotencyRecord


class VersionedFact(Protocol):
    """Immutable fact carrying an optimistic-concurrency version."""

    id: str
    version: int


FactT = TypeVar("FactT", bound=VersionedFact)


@dataclass(frozen=True, slots=True)
class FactKey:
    """Stable store identity independent of a persistence table key."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        for field, candidate in (("kind", self.kind), ("value", self.value)):
            if not isinstance(candidate, str):
                raise PortContractError(
                    resource="fact_key",
                    field=field,
                    reason="not_string",
                )
            if not candidate.strip():
                raise PortContractError(
                    resource="fact_key",
                    field=field,
                    reason="empty",
                )


@dataclass(frozen=True, slots=True)
class VersionedFactCommand[FactT: VersionedFact]:
    """Create or CAS one versioned fact under scoped idempotency."""

    key: FactKey
    expected_version: int | None
    fact: FactT
    idempotency: IdempotencyRecord

    def __post_init__(self) -> None:
        if self.expected_version is None:
            return
        if isinstance(self.expected_version, bool) or not isinstance(self.expected_version, int):
            raise PortContractError(
                resource="fact_command",
                field="expected_version",
                reason="not_integer",
            )
        if self.expected_version < 0:
            raise PortContractError(
                resource="fact_command",
                field="expected_version",
                reason="negative",
            )


@dataclass(frozen=True, slots=True)
class FactCommitResult[FactT: VersionedFact]:
    """The first durable fact result or its exact replay."""

    fact: FactT
    replayed: bool


@runtime_checkable
class VersionedFactStore(Protocol):
    """Persist immutable facts through create-or-CAS commands, never broad save/upsert."""

    async def get(self, key: FactKey) -> VersionedFact | None: ...

    async def commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]: ...


@runtime_checkable
class AttemptEventLog(Protocol):
    """Append immutable Worker events without update or delete operations."""

    async def append(self, attempt_id: str, event: AttemptEvent) -> ReplayResult[AttemptEvent]: ...

    async def list_for_attempt(self, attempt_id: str) -> tuple[AttemptEvent, ...]: ...
