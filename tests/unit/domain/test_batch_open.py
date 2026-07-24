"""T-M2-BATCH-001 (domain): Batch open binds active Suite revision.

Observable contract from T-M2-BATCH-001 / AC-MVP create path:
- open_for_suite pins project_id + suite_revision_id from an active Suite;
- retired Suite rejects new Batch binding (SuiteConflict suite_retired);
- unknown revision on the Suite is rejected;
- create identity carries request_digest + idempotency scope/key;
- digest mismatch on the same scoped key is a stable IdempotencyConflict;
- lifecycle-only Batch.create(batch_id) remains valid for existing pre-exec paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

CREATED_AT = datetime(2026, 7, 23, 19, tzinfo=UTC)
REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-request.v1",
        payload={"label": label},
    )


def _suite(*, retired: bool = False):
    from qarunner.domain import Suite, canonical_digest

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "source-v1"},
        ),
        config_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "config-v1"},
        ),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    if retired:
        suite = suite.retire(retired_at=RETIRED_AT, expected_version=suite.version)
    return suite


def test_open_for_suite_binds_active_current_revision() -> None:
    from qarunner.domain import Batch, BatchState

    suite = _suite()
    batch = Batch.open_for_suite(
        batch_id="batch-001",
        suite=suite,
        request_digest=_digest("create-v1"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-001",
        created_at=CREATED_AT,
    )

    assert batch.id == "batch-001"
    assert batch.state is BatchState.DRAFT
    assert batch.version == 0
    assert batch.project_id == "project-001"
    assert batch.suite_revision_id == "suite-revision-001"
    assert batch.request_digest == _digest("create-v1")
    assert batch.idempotency_scope == "project:project-001:batch-create"
    assert batch.idempotency_key == "idem-001"
    assert batch.created_at == CREATED_AT
    assert batch.priority_class == "background"
    assert batch.deadline_at is None
    assert batch.has_create_identity is True


def test_open_for_suite_can_pin_explicit_historical_revision() -> None:
    from qarunner.domain import Batch, canonical_digest

    suite = _suite()
    suite = suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "source-v2"},
        ),
        config_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "config-v2"},
        ),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=datetime(2026, 7, 23, 17, tzinfo=UTC),
        expected_version=suite.version,
    )
    batch = Batch.open_for_suite(
        batch_id="batch-002",
        suite=suite,
        suite_revision_id="suite-revision-001",
        request_digest=_digest("create-pin-v1"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-002",
        created_at=CREATED_AT,
    )
    assert batch.suite_revision_id == "suite-revision-001"
    assert suite.current_revision_id == "suite-revision-002"


def test_open_for_suite_rejects_retired_suite() -> None:
    from qarunner.domain import Batch, SuiteConflict

    suite = _suite(retired=True)
    with pytest.raises(SuiteConflict) as caught:
        Batch.open_for_suite(
            batch_id="batch-001",
            suite=suite,
            request_digest=_digest("create-v1"),
            idempotency_scope="project:project-001:batch-create",
            idempotency_key="idem-001",
            created_at=CREATED_AT,
        )
    assert caught.value.suite_id == "suite-001"
    assert caught.value.reason == "suite_retired"


def test_open_for_suite_rejects_unknown_revision() -> None:
    from qarunner.domain import Batch, SuiteConflict

    suite = _suite()
    with pytest.raises(SuiteConflict) as caught:
        Batch.open_for_suite(
            batch_id="batch-001",
            suite=suite,
            suite_revision_id="suite-revision-missing",
            request_digest=_digest("create-v1"),
            idempotency_scope="project:project-001:batch-create",
            idempotency_key="idem-001",
            created_at=CREATED_AT,
        )
    assert caught.value.reason == "revision_not_found"


def test_resolve_create_replay_matches_digest() -> None:
    from qarunner.domain import Batch

    batch = Batch.open_for_suite(
        batch_id="batch-001",
        suite=_suite(),
        request_digest=_digest("create-v1"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-001",
        created_at=CREATED_AT,
    )
    resolution = batch.resolve_create_replay(request_digest=_digest("create-v1"))
    assert resolution.replayed is True
    assert resolution.response_ref == "batch-001"
    assert resolution.response_status == 200


def test_resolve_create_replay_rejects_digest_conflict() -> None:
    from qarunner.domain import Batch, IdempotencyConflict

    batch = Batch.open_for_suite(
        batch_id="batch-001",
        suite=_suite(),
        request_digest=_digest("create-v1"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-001",
        created_at=CREATED_AT,
    )
    with pytest.raises(IdempotencyConflict) as caught:
        batch.resolve_create_replay(request_digest=_digest("create-v2"))
    assert caught.value.scope == "project:project-001:batch-create"
    assert caught.value.key == "idem-001"
    assert caught.value.stored_digest == _digest("create-v1")
    assert caught.value.received_digest == _digest("create-v2")


def test_lifecycle_create_without_identity_still_works() -> None:
    from qarunner.domain import Batch, BatchState

    batch = Batch.create(batch_id="batch-legacy")
    assert batch.id == "batch-legacy"
    assert batch.state is BatchState.DRAFT
    assert batch.version == 0
    assert batch.has_create_identity is False


def test_open_for_suite_rejects_non_suite() -> None:
    from qarunner.domain import Batch, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch.open_for_suite(
            batch_id="batch-001",
            suite=object(),  # type: ignore[arg-type]
            request_digest=_digest("create-v1"),
            idempotency_scope="project:project-001:batch-create",
            idempotency_key="idem-001",
            created_at=CREATED_AT,
        )
    assert caught.value.field == "suite"
    assert caught.value.reason == "not_suite"


def test_open_for_suite_accepts_deadline() -> None:
    from datetime import timedelta

    from qarunner.domain import Batch

    deadline = CREATED_AT + timedelta(hours=1)
    batch = Batch.open_for_suite(
        batch_id="batch-deadline",
        suite=_suite(),
        request_digest=_digest("create-deadline"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-deadline",
        created_at=CREATED_AT,
        deadline_at=deadline,
        priority_class="interactive",
    )
    assert batch.deadline_at == deadline
    assert batch.priority_class == "interactive"


def test_open_for_suite_rejects_blank_priority() -> None:
    from qarunner.domain import Batch, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch.open_for_suite(
            batch_id="batch-001",
            suite=_suite(),
            request_digest=_digest("create-v1"),
            idempotency_scope="project:project-001:batch-create",
            idempotency_key="idem-001",
            created_at=CREATED_AT,
            priority_class=" ",
        )
    assert caught.value.field == "priority_class"


def test_partial_create_identity_is_rejected() -> None:
    from qarunner.domain import Batch, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch(
            id="batch-partial",
            state=__import__("qarunner.domain", fromlist=["BatchState"]).BatchState.DRAFT,
            version=0,
            project_id="project-001",
        )
    assert caught.value.field == "create_identity"
    assert caught.value.reason == "partial"


def test_resolve_create_replay_requires_identity() -> None:
    from qarunner.domain import Batch, DomainValidationError

    batch = Batch.create(batch_id="batch-legacy")
    with pytest.raises(DomainValidationError) as caught:
        batch.resolve_create_replay(request_digest=_digest("x"))
    assert caught.value.reason == "create_identity_missing"
