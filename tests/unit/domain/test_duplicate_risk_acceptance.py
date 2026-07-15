from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import canonical_digest
from qarunner.domain.duplicate_risk_acceptance import (
    DuplicateRiskAcceptance,
    DuplicateRiskAcceptanceConsumption,
    DuplicateRiskAcceptanceRequest,
    DuplicateRiskReplay,
    consume_duplicate_risk_acceptance,
)
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _digest(label: str):
    return canonical_digest(schema_version="test.v1", payload={"label": label})


def _acceptance(**changes):
    values = dict(
        id="acceptance-1",
        run_id="run-1",
        source_attempt_id="attempt-1",
        source_attempt_no=1,
        source_fence=7,
        run_item_set_digest=_digest("items"),
        sut_identity="sut-1",
        sut_digest=_digest("sut"),
        target_grant_identity="grant-1",
        target_grant_version=3,
        target_grant_digest=_digest("grant"),
        retry_intent_digest=_digest("intent"),
        suite_owner_id="owner-1",
        reviewer_id="reviewer-1",
        original_executor_id="worker-1",
        original_trigger_actor_id="trigger-1",
        accepted_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    values.update(changes)
    return DuplicateRiskAcceptance(**values)


def _request(acceptance=None, **changes):
    acceptance = acceptance or _acceptance()
    values = dict(
        scope="run-retry",
        key="retry-key-1",
        acceptance=acceptance,
        requested_at=NOW + timedelta(minutes=30),
    )
    values.update(changes)
    return DuplicateRiskAcceptanceRequest(**values)


def test_acceptance_digest_binds_complete_canonical_scope() -> None:
    acceptance = _acceptance()
    assert acceptance.ttl == timedelta(hours=1)
    for field, value in (
        ("run_id", "run-2"),
        ("source_attempt_id", "attempt-2"),
        ("source_attempt_no", 2),
        ("source_fence", 8),
        ("run_item_set_digest", _digest("other-items")),
        ("sut_identity", "sut-2"),
        ("sut_digest", _digest("other-sut")),
        ("target_grant_identity", "grant-2"),
        ("target_grant_version", 4),
        ("target_grant_digest", _digest("other-grant")),
        ("retry_intent_digest", _digest("other-intent")),
        ("suite_owner_id", "owner-2"),
        ("reviewer_id", "reviewer-2"),
        ("original_executor_id", "worker-2"),
        ("original_trigger_actor_id", "trigger-2"),
    ):
        assert replace(acceptance, **{field: value}).digest != acceptance.digest
    shifted = replace(
        acceptance,
        accepted_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(hours=1, seconds=1),
    )
    assert shifted.digest != acceptance.digest


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "run_id",
        "source_attempt_id",
        "sut_identity",
        "target_grant_identity",
        "suite_owner_id",
        "reviewer_id",
        "original_executor_id",
        "original_trigger_actor_id",
    ],
)
@pytest.mark.parametrize("value", ["", 1])
def test_acceptance_rejects_invalid_strings(field, value) -> None:
    with pytest.raises(DomainValidationError):
        _acceptance(**{field: value})


@pytest.mark.parametrize("field", ["source_attempt_no", "source_fence", "target_grant_version"])
@pytest.mark.parametrize("value", [True, 0, "1"])
def test_acceptance_rejects_invalid_positive_integers(field, value) -> None:
    with pytest.raises(DomainValidationError):
        _acceptance(**{field: value})


@pytest.mark.parametrize(
    "field", ["run_item_set_digest", "sut_digest", "target_grant_digest", "retry_intent_digest"]
)
def test_acceptance_requires_typed_digests(field) -> None:
    with pytest.raises(DomainValidationError):
        _acceptance(**{field: "sha256:no"})


def test_acceptance_requires_utc_and_exact_one_hour_ttl() -> None:
    with pytest.raises(DomainValidationError):
        _acceptance(accepted_at=NOW.replace(tzinfo=None))
    with pytest.raises(DomainValidationError):
        _acceptance(expires_at=NOW.replace(tzinfo=None))
    with pytest.raises(DomainValidationError):
        _acceptance(expires_at=NOW + timedelta(minutes=59))


@pytest.mark.parametrize(
    "field", ["suite_owner_id", "original_executor_id", "original_trigger_actor_id"]
)
def test_reviewer_must_be_independent(field) -> None:
    with pytest.raises(DomainValidationError):
        _acceptance(**{field: "same"}, reviewer_id="same")


def test_request_and_consume_require_typed_values_and_valid_window() -> None:
    with pytest.raises(DomainValidationError):
        DuplicateRiskAcceptanceRequest("scope", "key", object(), NOW)
    with pytest.raises(DomainValidationError):
        consume_duplicate_risk_acceptance(request=object(), prior=None)
    with pytest.raises(DomainValidationError):
        consume_duplicate_risk_acceptance(
            request=_request(requested_at=NOW - timedelta(seconds=1)), prior=None
        )


def test_first_consumption_and_exact_historical_replay() -> None:
    request = _request()
    consumed = consume_duplicate_risk_acceptance(request=request, prior=None)
    assert isinstance(consumed, DuplicateRiskAcceptanceConsumption)
    assert consumed.scope == request.scope and consumed.request_digest == request.digest
    replay = consume_duplicate_risk_acceptance(request=request, prior=consumed)
    assert replay == DuplicateRiskReplay(consumption=consumed)
    assert replay.digest == DuplicateRiskReplay(consumption=consumed).digest
    assert replay.digest != consumed.digest


def test_consumption_and_replay_digests_bind_historical_fact() -> None:
    request = _request()
    consumed = consume_duplicate_risk_acceptance(request=request, prior=None)
    assert consumed.digest == replace(consumed).digest
    for field, value in (
        ("scope", "other"),
        ("key", "other"),
        ("acceptance_digest", _digest("other-acceptance")),
        ("request_digest", _digest("other-request")),
        ("consumed_at", consumed.consumed_at + timedelta(seconds=1)),
    ):
        assert replace(consumed, **{field: value}).digest != consumed.digest
    assert (
        DuplicateRiskReplay(replace(consumed, consumed_at=NOW + timedelta(seconds=1))).digest
        != DuplicateRiskReplay(consumed).digest
    )
    with pytest.raises(DomainValidationError):
        DuplicateRiskReplay(object())


def test_exact_historical_replay_remains_available_after_expiry() -> None:
    original = _request()
    consumed = consume_duplicate_risk_acceptance(request=original, prior=None)
    # Replay is determined from the frozen request digest, not from a new clock reading.
    assert consume_duplicate_risk_acceptance(request=original, prior=consumed) == (
        DuplicateRiskReplay(consumed)
    )


def test_expired_or_identity_changed_requests_fail_closed() -> None:
    with pytest.raises(DomainValidationError):
        consume_duplicate_risk_acceptance(
            request=_request(requested_at=NOW + timedelta(hours=1)), prior=None
        )
    request = _request()
    consumed = consume_duplicate_risk_acceptance(request=request, prior=None)
    changed = _request(replace(request.acceptance, sut_identity="sut-changed"))
    with pytest.raises(IdempotencyConflict):
        consume_duplicate_risk_acceptance(request=changed, prior=consumed)


def test_consumption_rejects_different_scope_key_or_digest_and_invalid_values() -> None:
    request = _request()
    prior = consume_duplicate_risk_acceptance(request=request, prior=None)
    for changed in (replace(request, scope="other"), replace(request, key="other")):
        with pytest.raises(IdempotencyConflict):
            consume_duplicate_risk_acceptance(request=changed, prior=prior)
    with pytest.raises(DomainValidationError):
        _request(scope="")
    with pytest.raises(DomainValidationError):
        _request(key=1)
    with pytest.raises(DomainValidationError):
        _request(requested_at="now")
    with pytest.raises(DomainValidationError):
        DuplicateRiskAcceptanceConsumption("", "key", _digest("a"), _digest("r"), NOW)
    with pytest.raises(DomainValidationError):
        DuplicateRiskAcceptanceConsumption("scope", "key", "a", _digest("r"), NOW)
    with pytest.raises(DomainValidationError):
        DuplicateRiskAcceptanceConsumption("scope", "key", _digest("a"), _digest("r"), "now")
    with pytest.raises(DomainValidationError):
        consume_duplicate_risk_acceptance(request=request, prior=object())
