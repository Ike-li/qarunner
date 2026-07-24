"""T-M2-SUITE-001 (domain): immutable Suite revisions and retire semantics.

Observable contract from AC-MVP-026 / T-M2-SUITE-001:
- register produces a first immutable revision that can be referenced;
- update produces a new immutable revision and does not rewrite historical
  revisions already produced;
- after suite retire, new Batch binding is rejected while history remains
  readable.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 23, 17, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-suite-revision.v1",
        payload={"label": label},
    )


def test_register_suite_produces_first_immutable_revision() -> None:
    from qarunner.domain import Suite, SuiteRevisionStatus, SuiteStatus

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )

    assert suite.id == "suite-001"
    assert suite.project_id == "project-001"
    assert suite.name == "shop-regression"
    assert suite.status is SuiteStatus.ACTIVE
    assert suite.version == 0
    assert suite.current_revision_id == "suite-revision-001"
    assert len(suite.revisions) == 1
    revision = suite.revisions[0]
    assert revision.id == "suite-revision-001"
    assert revision.suite_id == "suite-001"
    assert revision.revision_no == 1
    assert revision.status is SuiteRevisionStatus.APPROVED
    assert revision.source_spec_digest == _digest("source-v1")
    assert revision.config_digest == _digest("config-v1")
    assert revision.framework == "pytest"
    assert revision.resource_profile_id == "profile-default"
    assert revision.created_at == REGISTERED_AT
    assert suite.accepts_new_batch is True
    assert suite.current_revision is revision


def test_update_appends_new_revision_without_rewriting_history() -> None:
    from qarunner.domain import Suite, SuiteRevisionStatus

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    first = suite.revisions[0]

    updated = suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=_digest("source-v2"),
        config_digest=_digest("config-v2"),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=UPDATED_AT,
        expected_version=suite.version,
    )

    assert updated.version == suite.version + 1
    assert updated.current_revision_id == "suite-revision-002"
    assert len(updated.revisions) == 2
    # Historical revision object identity/content is preserved (not rewritten).
    assert updated.revisions[0] is first
    assert updated.revisions[0].id == "suite-revision-001"
    assert updated.revisions[0].revision_no == 1
    assert updated.revisions[0].source_spec_digest == _digest("source-v1")
    assert updated.revisions[0].config_digest == _digest("config-v1")
    second = updated.revisions[1]
    assert second.id == "suite-revision-002"
    assert second.revision_no == 2
    assert second.status is SuiteRevisionStatus.APPROVED
    assert second.source_spec_digest == _digest("source-v2")
    assert second.config_digest == _digest("config-v2")
    assert second.created_at == UPDATED_AT
    # Original suite aggregate remains unchanged (immutability of command result).
    assert suite.current_revision_id == "suite-revision-001"
    assert len(suite.revisions) == 1


def test_publish_revision_rejects_stale_expected_version() -> None:
    from qarunner.domain import Suite, VersionConflict

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )

    with pytest.raises(VersionConflict) as caught:
        suite.publish_revision(
            revision_id="suite-revision-002",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=99,
        )
    assert caught.value.entity_type == "suite"
    assert caught.value.entity_id == "suite-001"
    assert suite.current_revision_id == "suite-revision-001"


def test_publish_revision_rejects_duplicate_revision_id() -> None:
    from qarunner.domain import Suite, SuiteConflict

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )

    with pytest.raises(SuiteConflict) as caught:
        suite.publish_revision(
            revision_id="suite-revision-001",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=suite.version,
        )
    assert caught.value.reason == "revision_id_reused"


def test_retire_suite_rejects_new_batch_while_history_remains_readable() -> None:
    from qarunner.domain import Suite, SuiteConflict, SuiteStatus

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    updated = suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=_digest("source-v2"),
        config_digest=_digest("config-v2"),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=UPDATED_AT,
        expected_version=suite.version,
    )

    retired = updated.retire(
        retired_at=RETIRED_AT,
        expected_version=updated.version,
    )

    assert retired.status is SuiteStatus.RETIRED
    assert retired.version == updated.version + 1
    assert retired.accepts_new_batch is False
    assert len(retired.revisions) == 2
    assert retired.revisions[0].id == "suite-revision-001"
    assert retired.revisions[1].id == "suite-revision-002"
    assert retired.current_revision_id == "suite-revision-002"
    with pytest.raises(SuiteConflict) as caught:
        retired.require_accepts_new_batch()
    assert caught.value.reason == "suite_retired"
    # History still readable after retire.
    assert retired.revision("suite-revision-001").source_spec_digest == _digest("source-v1")


def test_retired_suite_rejects_publish_revision() -> None:
    from qarunner.domain import Suite, SuiteConflict

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    retired = suite.retire(retired_at=RETIRED_AT, expected_version=suite.version)

    with pytest.raises(SuiteConflict) as caught:
        retired.publish_revision(
            revision_id="suite-revision-002",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=retired.version,
        )
    assert caught.value.reason == "suite_retired"


def test_register_rejects_empty_name() -> None:
    from qarunner.domain import DomainValidationError, Suite

    with pytest.raises(DomainValidationError) as caught:
        Suite.register(
            suite_id="suite-001",
            project_id="project-001",
            name="  ",
            revision_id="suite-revision-001",
            source_spec_digest=_digest("source-v1"),
            config_digest=_digest("config-v1"),
            framework="pytest",
            resource_profile_id="profile-default",
            registered_at=REGISTERED_AT,
        )
    assert caught.value.field == "name"


def test_register_rejects_unsupported_framework() -> None:
    from qarunner.domain import DomainValidationError, Suite

    with pytest.raises(DomainValidationError) as caught:
        Suite.register(
            suite_id="suite-001",
            project_id="project-001",
            name="shop-regression",
            revision_id="suite-revision-001",
            source_spec_digest=_digest("source-v1"),
            config_digest=_digest("config-v1"),
            framework="junit4",
            resource_profile_id="profile-default",
            registered_at=REGISTERED_AT,
        )
    assert caught.value.field == "framework"


def test_revision_lookup_missing_id_raises() -> None:
    from qarunner.domain import Suite, SuiteConflict

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    with pytest.raises(SuiteConflict) as caught:
        suite.revision("missing")
    assert caught.value.reason == "revision_not_found"


def test_retire_is_idempotent_for_already_retired_suite_via_stale_version() -> None:
    from qarunner.domain import Suite, VersionConflict

    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    retired = suite.retire(retired_at=RETIRED_AT, expected_version=suite.version)
    with pytest.raises(VersionConflict):
        retired.retire(retired_at=RETIRED_AT, expected_version=suite.version)


def _base_suite():
    from qarunner.domain import Suite

    return Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )


def _unsafe_suite_replace(suite, **changes):
    unsafe = object.__new__(type(suite))
    for field in (
        "id",
        "project_id",
        "name",
        "status",
        "version",
        "revisions",
        "current_revision_id",
        "retired_at",
    ):
        object.__setattr__(
            unsafe,
            field,
            changes[field] if field in changes else getattr(suite, field),
        )
    type(suite).__post_init__(unsafe)
    return unsafe


def _unsafe_revision_replace(revision, **changes):
    unsafe = object.__new__(type(revision))
    for field in (
        "id",
        "suite_id",
        "revision_no",
        "source_spec_digest",
        "config_digest",
        "framework",
        "resource_profile_id",
        "status",
        "created_at",
    ):
        object.__setattr__(
            unsafe,
            field,
            changes[field] if field in changes else getattr(revision, field),
        )
    type(revision).__post_init__(unsafe)
    return unsafe


def test_retire_already_retired_with_current_version_raises_conflict() -> None:
    from qarunner.domain import SuiteConflict

    suite = _base_suite()
    retired = suite.retire(retired_at=RETIRED_AT, expected_version=suite.version)
    with pytest.raises(SuiteConflict) as caught:
        retired.retire(retired_at=RETIRED_AT, expected_version=retired.version)
    assert caught.value.reason == "suite_already_retired"


def test_suite_revision_rejects_invalid_revision_no() -> None:
    from qarunner.domain import DomainValidationError, SuiteRevision, SuiteRevisionStatus

    revision = _base_suite().revisions[0]
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_revision_replace(revision, revision_no=0)
    assert caught.value.field == "revision_no"


def test_suite_revision_rejects_non_digest_fields() -> None:
    from qarunner.domain import DomainValidationError

    revision = _base_suite().revisions[0]
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_revision_replace(revision, source_spec_digest="not-a-digest")
    assert caught.value.field == "source_spec_digest"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_revision_replace(revision, config_digest="not-a-digest")
    assert caught.value.field == "config_digest"


def test_suite_revision_rejects_unknown_status() -> None:
    from qarunner.domain import DomainValidationError

    revision = _base_suite().revisions[0]
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_revision_replace(revision, status="nope")
    assert caught.value.field == "status"


def test_suite_rejects_invalid_status_and_version() -> None:
    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, status="nope")
    assert caught.value.field == "status"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, version=-1)
    assert caught.value.field == "version"


def test_suite_rejects_empty_or_invalid_revisions() -> None:
    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, revisions=())
    assert caught.value.field == "revisions"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, revisions=(object(),))  # type: ignore[arg-type]
    assert caught.value.field == "revisions"


def test_suite_rejects_duplicate_and_noncontiguous_revision_nos() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    first = suite.revisions[0]
    second = replace(
        first,
        id="suite-revision-002",
        revision_no=1,
        created_at=UPDATED_AT,
    )
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            revisions=(first, second),
            current_revision_id="suite-revision-002",
        )
    assert caught.value.field in {"revisions", "current_revision_id"}
    third = replace(first, id="suite-revision-003", revision_no=3, created_at=UPDATED_AT)
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            revisions=(first, third),
            current_revision_id="suite-revision-003",
        )
    assert caught.value.field == "revisions"


def test_suite_rejects_suite_id_mismatch_and_missing_current() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    first = suite.revisions[0]
    mismatched = replace(first, suite_id="other-suite")
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, revisions=(mismatched,))
    assert caught.value.field == "revisions"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, current_revision_id="missing")
    assert caught.value.field == "current_revision_id"


def test_suite_rejects_current_not_latest_and_retired_at_rules() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError, SuiteStatus

    suite = _base_suite()
    first = suite.revisions[0]
    second = replace(
        first,
        id="suite-revision-002",
        revision_no=2,
        created_at=UPDATED_AT,
    )
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            revisions=(first, second),
            current_revision_id="suite-revision-001",
        )
    assert caught.value.field == "current_revision_id"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, status=SuiteStatus.RETIRED, retired_at=None)
    assert caught.value.field == "retired_at"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, retired_at=RETIRED_AT)
    assert caught.value.field == "retired_at"


def test_suite_rejects_non_monotonic_revision_timeline() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    first = suite.revisions[0]
    earlier = replace(
        first,
        id="suite-revision-002",
        revision_no=2,
        created_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            revisions=(first, earlier),
            current_revision_id="suite-revision-002",
        )
    assert caught.value.field == "revisions"


def test_helpers_reject_non_string_and_non_utc() -> None:
    from datetime import datetime

    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(suite, name=123)  # type: ignore[arg-type]
    assert caught.value.field == "name"
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            status=suite.status.__class__("retired"),
            retired_at=datetime(2026, 7, 23, 18),
        )
    assert caught.value.field == "retired_at"


def test_suite_rejects_duplicate_revision_ids() -> None:
    from dataclasses import replace

    from qarunner.domain import DomainValidationError

    suite = _base_suite()
    first = suite.revisions[0]
    # Same id, different revision_no so contiguity is not the first failure.
    twin = replace(first, revision_no=2, created_at=UPDATED_AT)
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            revisions=(first, twin),
            current_revision_id=first.id,
        )
    assert caught.value.field == "revisions"
    assert caught.value.reason == "duplicate_id"


def test_helpers_reject_non_datetime_for_utc_field() -> None:
    from qarunner.domain import DomainValidationError, SuiteStatus

    suite = _base_suite()
    with pytest.raises(DomainValidationError) as caught:
        _unsafe_suite_replace(
            suite,
            status=SuiteStatus.RETIRED,
            retired_at="not-a-datetime",  # type: ignore[arg-type]
        )
    assert caught.value.field == "retired_at"
    assert caught.value.reason == "not_datetime"
