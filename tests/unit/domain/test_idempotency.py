"""T-M0-IDEM-001: canonical request digests and idempotent replay."""

import pytest


def test_digest_accepts_only_canonical_lowercase_sha256() -> None:
    from qarunner.domain import Digest

    value = f"sha256:{'a' * 64}"

    assert Digest(value).value == value


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        pytest.param(123, "not_string", id="not-string"),
        pytest.param("", "invalid_sha256", id="empty"),
        pytest.param("a" * 64, "invalid_sha256", id="missing-algorithm"),
        pytest.param(f"sha256:{'a' * 63}", "invalid_sha256", id="too-short"),
        pytest.param(f"sha256:{'a' * 65}", "invalid_sha256", id="too-long"),
        pytest.param(f"sha256:{'A' * 64}", "invalid_sha256", id="uppercase"),
        pytest.param(f"sha256:{'g' * 64}", "invalid_sha256", id="non-hex"),
        pytest.param(f"sha512:{'a' * 64}", "invalid_sha256", id="wrong-algorithm"),
    ],
)
def test_digest_rejects_noncanonical_values(value: object, reason: str) -> None:
    from qarunner.domain import Digest, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Digest(value)  # type: ignore[arg-type]

    assert caught.value.entity_type == "digest"
    assert caught.value.field == "value"
    assert caught.value.reason == reason


def test_canonical_digest_is_independent_of_object_insertion_order() -> None:
    """Semantically identical request objects produce the same digest."""
    from qarunner.domain import canonical_digest

    first = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"suite_revision_id": "suite-rev-001", "priority": 10},
    )
    second = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"priority": 10, "suite_revision_id": "suite-rev-001"},
    )

    assert first == second
    assert first.value.startswith("sha256:")
    assert len(first.value) == 71


def test_canonical_digest_rejects_floating_point_values() -> None:
    """The M0 canonical value domain avoids ambiguous number encodings."""
    from qarunner.domain import CanonicalizationError, canonical_digest

    with pytest.raises(CanonicalizationError) as caught:
        canonical_digest(
            schema_version="qep.batch-request.v1",
            payload={"estimated_seconds": 1.5},
        )

    assert caught.value.code == "canonicalization_error"
    assert caught.value.path == "$.payload.estimated_seconds"


@pytest.mark.parametrize(
    ("payload", "expected_path"),
    [
        ({"value": (1, 2)}, "$.payload.value"),
        ({"value": {1, 2}}, "$.payload.value"),
        ({1: "not-a-string-key"}, "$.payload"),
        ({"value": 9_007_199_254_740_992}, "$.payload.value"),
        ({"value": "\ud800"}, "$.payload.value"),
    ],
)
def test_canonical_digest_rejects_values_outside_frozen_json_domain(
    payload: object, expected_path: str
) -> None:
    """Only I-JSON-safe integers and the declared container types are accepted."""
    from qarunner.domain import CanonicalizationError, canonical_digest

    with pytest.raises(CanonicalizationError) as caught:
        canonical_digest(
            schema_version="qep.batch-request.v1",
            payload=payload,  # type: ignore[arg-type]
        )

    assert caught.value.code == "canonicalization_error"
    assert caught.value.path == expected_path


def test_canonical_digest_rejects_invalid_unicode_object_keys() -> None:
    from qarunner.domain import CanonicalizationError, canonical_digest

    with pytest.raises(CanonicalizationError) as caught:
        canonical_digest(
            schema_version="qep.batch-request.v1",
            payload={"nested": {"\ud800": "invalid-key"}},
        )

    assert caught.value.path == "$.payload.nested.<key>"
    assert caught.value.reason == "string is not valid Unicode"


@pytest.mark.parametrize(
    ("schema_version", "reason"),
    [
        pytest.param(1, "schema version must be a string", id="not-string"),
        pytest.param("\ud800", "string is not valid Unicode", id="invalid-unicode"),
    ],
)
def test_canonical_digest_rejects_invalid_schema_versions(
    schema_version: object, reason: str
) -> None:
    from qarunner.domain import CanonicalizationError, canonical_digest

    with pytest.raises(CanonicalizationError) as caught:
        canonical_digest(
            schema_version=schema_version,  # type: ignore[arg-type]
            payload={},
        )

    assert caught.value.path == "$.schema_version"
    assert caught.value.reason == reason


def test_canonical_digest_preserves_array_order_and_includes_schema_version() -> None:
    """Arrays remain ordered while every supported primitive has stable encoding."""
    from qarunner.domain import canonical_digest

    payload = {
        "values": [None, True, False, 0, -10, "测试", {"nested": []}],
    }

    original = canonical_digest(schema_version="qep.batch-request.v1", payload=payload)
    reordered = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"values": list(reversed(payload["values"]))},
    )
    newer_schema = canonical_digest(schema_version="qep.batch-request.v2", payload=payload)

    assert original != reordered
    assert original != newer_schema


def test_same_idempotency_key_and_digest_replays_the_original_response() -> None:
    """A lost response can be retried without creating a second result."""
    from qarunner.domain import IdempotencyRecord, canonical_digest

    request_digest = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"suite_revision_id": "suite-rev-001"},
    )
    record = IdempotencyRecord.create(
        scope="principal:user-001",
        key="request-001",
        request_digest=request_digest,
        response_status=202,
        response_ref="batch-001",
    )

    replay = record.resolve(request_digest=request_digest)

    assert replay.response_status == 202
    assert replay.response_ref == "batch-001"
    assert replay.replayed is True


def test_same_idempotency_key_with_a_different_digest_is_rejected() -> None:
    """Changing request content cannot reuse a key to create a second intent."""
    from qarunner.domain import IdempotencyConflict, IdempotencyRecord, canonical_digest

    original_digest = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"suite_revision_id": "suite-rev-001"},
    )
    changed_digest = canonical_digest(
        schema_version="qep.batch-request.v1",
        payload={"suite_revision_id": "suite-rev-002"},
    )
    record = IdempotencyRecord.create(
        scope="principal:user-001",
        key="request-001",
        request_digest=original_digest,
        response_status=202,
        response_ref="batch-001",
    )

    with pytest.raises(IdempotencyConflict) as caught:
        record.resolve(request_digest=changed_digest)

    assert caught.value.code == "idempotency_conflict"
    assert caught.value.scope == "principal:user-001"
    assert caught.value.key == "request-001"
    assert caught.value.stored_digest == original_digest
    assert caught.value.received_digest == changed_digest
