"""M0 execution-authority value contract."""

from dataclasses import replace

import pytest

from qarunner.domain import AttemptAuthority, DomainValidationError, WorkerRef


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"current_fence": 0}, "current_fence", "not_positive"),
        ({"current_fence": -1}, "current_fence", "not_positive"),
        ({"current_fence": True}, "current_fence", "not_integer"),
        ({"current_fence": "1"}, "current_fence", "not_integer"),
        ({"current_worker": None}, "current_worker", "invalid_type"),
    ],
)
def test_attempt_authority_rejects_invalid_fence_or_worker(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    authority = AttemptAuthority(
        current_fence=1,
        current_worker=WorkerRef(worker_id="worker-1", generation=1),
    )

    with pytest.raises(DomainValidationError) as captured:
        replace(authority, **changes)

    assert captured.value.entity_type == "attempt_authority"
    assert captured.value.field == field
    assert captured.value.reason == reason
