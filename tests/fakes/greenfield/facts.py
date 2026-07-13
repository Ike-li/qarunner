"""In-memory versioned fact-store Fake."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.facts import (
    FactCommitResult,
    FactKey,
    FactT,
    VersionedFact,
    VersionedFactCommand,
)
from qarunner.domain.errors import EventConflict, ensure_expected_version
from qarunner.domain.event import AttemptEvent
from qarunner.domain.idempotency import IdempotencyRecord


class InMemoryVersionedFactStore:
    """Keep immutable fact snapshots in memory for deterministic application tests."""

    def __init__(self) -> None:
        self.__facts: dict[FactKey, VersionedFact] = {}
        self.__commands: dict[
            tuple[str, str],
            tuple[
                VersionedFactCommand[VersionedFact],
                IdempotencyRecord,
                FactCommitResult[VersionedFact],
            ],
        ] = {}

    async def get(self, key: FactKey) -> VersionedFact | None:
        return self.__facts.get(key)

    async def commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]:
        command_key = (command.idempotency.scope, command.idempotency.key)
        replay = self.__commands.get(command_key)
        if replay is not None:
            stored_command, stored_record, stored_result = replay
            stored_record.resolve(request_digest=command.idempotency.request_digest)
            if stored_command != command:
                raise PortContractError(
                    resource="fact_store",
                    field="idempotency",
                    reason="non_exact_replay",
                )
            return cast(FactCommitResult[FactT], replace(stored_result, replayed=True))
        if command.key.value != command.fact.id:
            raise PortContractError(
                resource="fact_store",
                field="key.value",
                reason="fact_id_mismatch",
            )
        current = self.__facts.get(command.key)
        if command.expected_version is None:
            if current is not None:
                raise PortContractError(
                    resource="fact_store",
                    field="expected_version",
                    reason="already_exists",
                )
        else:
            if current is None:
                raise PortContractError(
                    resource="fact_store",
                    field="key",
                    reason="not_found",
                )
            ensure_expected_version(
                entity_type=command.key.kind,
                entity_id=command.key.value,
                current_version=current.version,
                expected_version=command.expected_version,
            )
        required_version = 0 if command.expected_version is None else command.expected_version + 1
        if command.fact.version != required_version:
            raise PortContractError(
                resource="fact_store",
                field="fact.version",
                reason="not_next_version",
            )
        self.__facts[command.key] = command.fact
        result = FactCommitResult(fact=command.fact, replayed=False)
        self.__commands[command_key] = (
            cast(VersionedFactCommand[VersionedFact], command),
            command.idempotency,
            cast(FactCommitResult[VersionedFact], result),
        )
        return result


class InMemoryAttemptEventLog:
    """Append-only Attempt events with deterministic stream snapshots."""

    def __init__(self) -> None:
        self.__streams: dict[str, list[AttemptEvent]] = {}

    async def append(self, attempt_id: str, event: AttemptEvent) -> ReplayResult[AttemptEvent]:
        stream = self.__streams.setdefault(attempt_id, [])
        for stored_event in stream:
            if stored_event == event:
                return ReplayResult(value=stored_event, replayed=True)
            if (
                stored_event.event_id == event.event_id
                or stored_event.event_seq == event.event_seq
            ):
                raise EventConflict(
                    attempt_id=attempt_id,
                    stored_event=stored_event,
                    received_event=event,
                )
        stream.append(event)
        return ReplayResult(value=event, replayed=False)

    async def list_for_attempt(self, attempt_id: str) -> tuple[AttemptEvent, ...]:
        return tuple(self.__streams.get(attempt_id, ()))
