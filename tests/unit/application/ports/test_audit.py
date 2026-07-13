"""T-M0-PORT-001 append-only audit contract."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tests.fakes.greenfield.audit import InMemoryAuditLog

from qarunner.application.ports.audit import AuditLog, AuditRecord
from qarunner.application.ports.common import PortContractError
from qarunner.domain import Digest


async def test_audit_log_appends_one_record_and_returns_an_immutable_snapshot() -> None:
    record = _record()
    log = InMemoryAuditLog()

    result = await log.append(record)

    assert isinstance(log, AuditLog)
    assert result.value == record
    assert result.replayed is False
    assert await log.list_for_object("run", "run-1") == (record,)


async def test_audit_log_replays_an_exact_record_without_appending() -> None:
    record = _record()
    log = InMemoryAuditLog()
    await log.append(record)

    replay = await log.append(replace(record))

    assert replay.value == record
    assert replay.replayed is True
    assert await log.list_for_object("run", "run-1") == (record,)


async def test_audit_log_rejects_reused_event_id_without_changing_history() -> None:
    record = _record()
    conflict = replace(record, decision="denied", reason="policy_denied")
    log = InMemoryAuditLog()
    await log.append(record)

    with pytest.raises(PortContractError) as captured:
        await log.append(conflict)

    assert captured.value.resource == "audit_log"
    assert captured.value.field == "event_id"
    assert captured.value.reason == "conflicting_content"
    assert await log.list_for_object("run", "run-1") == (record,)


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"actor_id": ""}, "actor_id", "empty"),
        (
            {"actor_id": "user-1\nforged-event"},
            "actor_id",
            "contains_control_character",
        ),
        ({"request_id": 7}, "request_id", "not_string"),
        (
            {"reason": "Authorization: Bearer plaintext-secret"},
            "reason",
            "not_code",
        ),
        (
            {"occurred_at": datetime(2026, 7, 13, 12, 0)},
            "occurred_at",
            "not_utc",
        ),
        ({"after_digest": "sha256:not-a-value"}, "after_digest", "not_digest"),
    ],
)
def test_audit_record_rejects_invalid_or_unbounded_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    with pytest.raises(PortContractError) as captured:
        replace(_record(), **changes)

    assert captured.value.resource == "audit_record"
    assert captured.value.field == field
    assert captured.value.reason == reason


def _record() -> AuditRecord:
    return AuditRecord(
        event_id="audit-1",
        actor_id="user-1",
        authentication_strength="oidc-mfa",
        request_id="request-1",
        object_kind="run",
        object_id="run-1",
        action="queue",
        decision="allowed",
        reason="request_valid",
        before_digest=None,
        after_digest=Digest("sha256:" + "1" * 64),
        occurred_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        source="api",
    )
