"""T-M0-PORT-001 versioned fact-store contract."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.facts import InMemoryVersionedFactStore

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.facts import (
    FactKey,
    VersionedFactCommand,
    VersionedFactStore,
)
from qarunner.domain import (
    Digest,
    IdempotencyConflict,
    IdempotencyRecord,
    Run,
    RunState,
    VersionConflict,
)


@pytest.mark.parametrize(
    ("kind", "value", "field", "reason"),
    [
        ("", "run-1", "kind", "empty"),
        (1, "run-1", "kind", "not_string"),
        ("run", "   ", "value", "empty"),
        ("run", None, "value", "not_string"),
    ],
)
def test_fact_key_rejects_invalid_identity_values(
    kind: object,
    value: object,
    field: str,
    reason: str,
) -> None:
    with pytest.raises(PortContractError) as captured:
        FactKey(kind=kind, value=value)  # type: ignore[arg-type]

    assert captured.value.resource == "fact_key"
    assert captured.value.field == field
    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("expected_version", "reason"),
    [
        (True, "not_integer"),
        (1.5, "not_integer"),
        (-1, "negative"),
    ],
)
def test_fact_command_rejects_invalid_expected_version(
    expected_version: object,
    reason: str,
) -> None:
    run = Run.create(run_id="run-1")

    with pytest.raises(PortContractError) as captured:
        VersionedFactCommand(
            key=FactKey(kind="run", value=run.id),
            expected_version=expected_version,  # type: ignore[arg-type]
            fact=run,
            idempotency=_record("create", "1"),
        )

    assert captured.value.resource == "fact_command"
    assert captured.value.field == "expected_version"
    assert captured.value.reason == reason


async def test_fact_store_creates_and_reads_one_immutable_run_snapshot() -> None:
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=IdempotencyRecord.create(
            scope="run:create",
            key="create-run-1",
            request_digest=Digest("sha256:" + "1" * 64),
            response_status=201,
            response_ref=run.id,
        ),
    )
    store = InMemoryVersionedFactStore()

    result = await store.commit(command)

    assert isinstance(store, VersionedFactStore)
    assert result.fact == run
    assert result.replayed is False
    assert await store.get(command.key) == run


async def test_fact_store_rejects_cas_for_a_missing_fact() -> None:
    store = InMemoryVersionedFactStore()
    run = replace(Run.create(run_id="run-1"), version=1)
    key = FactKey(kind="run", value=run.id)

    with pytest.raises(PortContractError) as captured:
        await store.commit(
            VersionedFactCommand(
                key=key,
                expected_version=0,
                fact=run,
                idempotency=_record("update-missing", "1"),
            )
        )

    assert captured.value.resource == "fact_store"
    assert captured.value.field == "key"
    assert captured.value.reason == "not_found"
    assert await store.get(key) is None


async def test_fact_store_requires_version_zero_for_create() -> None:
    store = InMemoryVersionedFactStore()
    run = replace(Run.create(run_id="run-1"), version=1)
    key = FactKey(kind="run", value=run.id)

    with pytest.raises(PortContractError) as captured:
        await store.commit(
            VersionedFactCommand(
                key=key,
                expected_version=None,
                fact=run,
                idempotency=_record("create", "1"),
            )
        )

    assert captured.value.resource == "fact_store"
    assert captured.value.field == "fact.version"
    assert captured.value.reason == "not_next_version"
    assert await store.get(key) is None


async def test_fact_store_rejects_stale_cas_without_mutating_the_snapshot() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    await store.commit(
        VersionedFactCommand(
            key=key,
            expected_version=None,
            fact=run,
            idempotency=_record("create", "1"),
        )
    )
    queued = run.transition(RunState.QUEUED, expected_version=0)
    await store.commit(
        VersionedFactCommand(
            key=key,
            expected_version=0,
            fact=queued,
            idempotency=_record("queue", "2"),
        )
    )

    with pytest.raises(VersionConflict):
        await store.commit(
            VersionedFactCommand(
                key=key,
                expected_version=0,
                fact=queued,
                idempotency=_record("stale", "3"),
            )
        )

    assert await store.get(key) == queued


async def test_fact_store_exact_replay_wins_before_current_version_checks() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    create = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=_record("create", "1"),
    )
    first = await store.commit(create)
    queued = run.transition(RunState.QUEUED, expected_version=0)
    await store.commit(
        VersionedFactCommand(
            key=key,
            expected_version=0,
            fact=queued,
            idempotency=_record("queue", "2"),
        )
    )

    replay = await store.commit(create)

    assert replay.fact == first.fact
    assert replay.replayed is True
    assert await store.get(key) == queued


async def test_fact_store_replays_a_new_but_equal_command_value() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    command = VersionedFactCommand(
        key=FactKey(kind="run", value=run.id),
        expected_version=None,
        fact=run,
        idempotency=_record("create", "1"),
    )
    await store.commit(command)

    replay = await store.commit(replace(command))

    assert replay.fact == run
    assert replay.replayed is True


async def test_fact_store_rejects_a_second_create_without_overwriting() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    await store.commit(
        VersionedFactCommand(
            key=key,
            expected_version=None,
            fact=run,
            idempotency=_record("create-1", "1"),
        )
    )

    with pytest.raises(PortContractError) as captured:
        await store.commit(
            VersionedFactCommand(
                key=key,
                expected_version=None,
                fact=run,
                idempotency=_record("create-2", "2"),
            )
        )

    assert captured.value.reason == "already_exists"
    assert await store.get(key) == run


async def test_fact_store_rejects_a_non_monotonic_next_version() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    await store.commit(
        VersionedFactCommand(
            key=key,
            expected_version=None,
            fact=run,
            idempotency=_record("create", "1"),
        )
    )
    skipped = replace(run.transition(RunState.QUEUED, expected_version=0), version=2)

    with pytest.raises(PortContractError) as captured:
        await store.commit(
            VersionedFactCommand(
                key=key,
                expected_version=0,
                fact=skipped,
                idempotency=_record("skip", "2"),
            )
        )

    assert captured.value.field == "fact.version"
    assert captured.value.reason == "not_next_version"
    assert await store.get(key) == run


async def test_fact_store_rejects_same_scoped_key_with_a_different_digest() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-1")
    key = FactKey(kind="run", value=run.id)
    first = VersionedFactCommand(
        key=key,
        expected_version=None,
        fact=run,
        idempotency=_record("same-key", "1"),
    )
    await store.commit(first)
    conflict = replace(first, idempotency=_record("same-key", "2"))

    with pytest.raises(IdempotencyConflict):
        await store.commit(conflict)

    assert await store.get(key) == run


async def test_fact_store_rejects_same_digest_when_the_command_is_not_exact() -> None:
    store = InMemoryVersionedFactStore()
    first = Run.create(run_id="run-1")
    second = Run.create(run_id="run-2")
    idempotency = _record("same-key", "1")
    await store.commit(
        VersionedFactCommand(
            key=FactKey(kind="run", value=first.id),
            expected_version=None,
            fact=first,
            idempotency=idempotency,
        )
    )
    changed_command = VersionedFactCommand(
        key=FactKey(kind="run", value=second.id),
        expected_version=None,
        fact=second,
        idempotency=idempotency,
    )

    with pytest.raises(PortContractError) as captured:
        await store.commit(changed_command)

    assert captured.value.resource == "fact_store"
    assert captured.value.field == "idempotency"
    assert captured.value.reason == "non_exact_replay"
    assert await store.get(FactKey(kind="run", value=first.id)) == first
    assert await store.get(FactKey(kind="run", value=second.id)) is None


async def test_fact_store_rejects_a_fact_bound_to_a_different_key() -> None:
    store = InMemoryVersionedFactStore()
    run = Run.create(run_id="run-2")
    wrong_key = FactKey(kind="run", value="run-1")

    with pytest.raises(PortContractError) as captured:
        await store.commit(
            VersionedFactCommand(
                key=wrong_key,
                expected_version=None,
                fact=run,
                idempotency=_record("wrong-key", "1"),
            )
        )

    assert captured.value.resource == "fact_store"
    assert captured.value.field == "key.value"
    assert captured.value.reason == "fact_id_mismatch"
    assert await store.get(wrong_key) is None


async def test_fact_store_scopes_the_same_literal_idempotency_key_independently() -> None:
    store = InMemoryVersionedFactStore()
    first = Run.create(run_id="run-1")
    second = Run.create(run_id="run-2")

    await store.commit(
        VersionedFactCommand(
            key=FactKey(kind="run", value=first.id),
            expected_version=None,
            fact=first,
            idempotency=_record("same-key", "1", scope="project:a"),
        )
    )
    result = await store.commit(
        VersionedFactCommand(
            key=FactKey(kind="run", value=second.id),
            expected_version=None,
            fact=second,
            idempotency=_record("same-key", "2", scope="project:b"),
        )
    )

    assert result.fact == second
    assert result.replayed is False


def _record(key: str, digest_digit: str, *, scope: str = "run:test") -> IdempotencyRecord:
    return IdempotencyRecord.create(
        scope=scope,
        key=key,
        request_digest=Digest("sha256:" + digest_digit * 64),
        response_status=200,
        response_ref="run-1",
    )
