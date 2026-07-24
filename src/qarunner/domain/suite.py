"""Suite aggregate: immutable revisions and retire semantics (T-M2-SUITE-001).

M2 single-ECS scope. Maps to AC-MVP-026 / MVP-FR-001:
register produces the first immutable revision; update appends a new revision
without rewriting history; retire rejects new Batch binding while history stays
readable. Persistence/API adapters are later M2 sub-gates.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError, SuiteConflict, ensure_expected_version

_SUPPORTED_FRAMEWORKS = frozenset({"pytest", "playwright"})


class SuiteStatus(enum.StrEnum):
    """Lifecycle of one Suite aggregate (not of a single revision)."""

    ACTIVE = "active"
    RETIRED = "retired"


class SuiteRevisionStatus(enum.StrEnum):
    """Status of one immutable Suite revision row.

    M2 domain starts revisions as approved so they are immediately bindable for
    Batch create; draft approval workflow is deferred.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class SuiteRevision:
    """One immutable Suite configuration snapshot."""

    id: str
    suite_id: str
    revision_no: int
    source_spec_digest: Digest
    config_digest: Digest
    framework: str
    resource_profile_id: str
    status: SuiteRevisionStatus
    created_at: datetime

    def __post_init__(self) -> None:
        _require_nonempty_string("suite_revision", "id", self.id)
        _require_nonempty_string("suite_revision", "suite_id", self.suite_id)
        if (
            isinstance(self.revision_no, bool)
            or not isinstance(self.revision_no, int)
            or self.revision_no < 1
        ):
            _invalid("suite_revision", "revision_no", "invalid")
        if not isinstance(self.source_spec_digest, Digest):
            _invalid("suite_revision", "source_spec_digest", "not_digest")
        if not isinstance(self.config_digest, Digest):
            _invalid("suite_revision", "config_digest", "not_digest")
        _require_nonempty_string("suite_revision", "framework", self.framework)
        if self.framework not in _SUPPORTED_FRAMEWORKS:
            _invalid("suite_revision", "framework", "unsupported")
        _require_nonempty_string("suite_revision", "resource_profile_id", self.resource_profile_id)
        if not isinstance(self.status, SuiteRevisionStatus):
            _invalid("suite_revision", "status", "unknown")
        _require_utc("suite_revision", "created_at", self.created_at)


@dataclass(frozen=True, slots=True)
class Suite:
    """Immutable Suite aggregate; successful commands return a new version."""

    id: str
    project_id: str
    name: str
    status: SuiteStatus
    version: int
    revisions: tuple[SuiteRevision, ...]
    current_revision_id: str
    retired_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string("suite", "id", self.id)
        _require_nonempty_string("suite", "project_id", self.project_id)
        _require_nonempty_string("suite", "name", self.name)
        if not isinstance(self.status, SuiteStatus):
            _invalid("suite", "status", "unknown")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            _invalid("suite", "version", "invalid")
        if not isinstance(self.revisions, tuple) or not self.revisions:
            _invalid("suite", "revisions", "empty")
        if any(not isinstance(item, SuiteRevision) for item in self.revisions):
            _invalid("suite", "revisions", "invalid_type")
        ids = tuple(item.id for item in self.revisions)
        if len(ids) != len(set(ids)):
            _invalid("suite", "revisions", "duplicate_id")
        numbers = tuple(item.revision_no for item in self.revisions)
        if numbers != tuple(range(1, len(self.revisions) + 1)):
            _invalid("suite", "revisions", "revision_no_not_contiguous")
        if any(item.suite_id != self.id for item in self.revisions):
            _invalid("suite", "revisions", "suite_id_mismatch")
        _require_nonempty_string("suite", "current_revision_id", self.current_revision_id)
        if self.current_revision_id not in ids:
            _invalid("suite", "current_revision_id", "not_found")
        if self.revisions[-1].id != self.current_revision_id:
            _invalid("suite", "current_revision_id", "not_latest")
        if self.status is SuiteStatus.RETIRED:
            if self.retired_at is None:
                _invalid("suite", "retired_at", "required_for_retired")
            _require_utc("suite", "retired_at", self.retired_at)
        elif self.retired_at is not None:
            _invalid("suite", "retired_at", "forbidden_for_active")
        previous_created: datetime | None = None
        for item in self.revisions:
            if previous_created is not None and item.created_at < previous_created:
                _invalid("suite", "revisions", "timeline_not_monotonic")
            previous_created = item.created_at

    @classmethod
    def register(
        cls,
        *,
        suite_id: str,
        project_id: str,
        name: str,
        revision_id: str,
        source_spec_digest: Digest,
        config_digest: Digest,
        framework: str,
        resource_profile_id: str,
        registered_at: datetime,
    ) -> Suite:
        """Create a Suite with its first approved revision."""
        revision = SuiteRevision(
            id=revision_id,
            suite_id=suite_id,
            revision_no=1,
            source_spec_digest=source_spec_digest,
            config_digest=config_digest,
            framework=framework,
            resource_profile_id=resource_profile_id,
            status=SuiteRevisionStatus.APPROVED,
            created_at=registered_at,
        )
        return cls(
            id=suite_id,
            project_id=project_id,
            name=name,
            status=SuiteStatus.ACTIVE,
            version=0,
            revisions=(revision,),
            current_revision_id=revision_id,
        )

    @property
    def current_revision(self) -> SuiteRevision:
        return self.revision(self.current_revision_id)

    @property
    def accepts_new_batch(self) -> bool:
        return self.status is SuiteStatus.ACTIVE

    def revision(self, revision_id: str) -> SuiteRevision:
        for item in self.revisions:
            if item.id == revision_id:
                return item
        raise SuiteConflict(suite_id=self.id, reason="revision_not_found")

    def require_accepts_new_batch(self) -> None:
        if not self.accepts_new_batch:
            raise SuiteConflict(suite_id=self.id, reason="suite_retired")

    def publish_revision(
        self,
        *,
        revision_id: str,
        source_spec_digest: Digest,
        config_digest: Digest,
        framework: str,
        resource_profile_id: str,
        created_at: datetime,
        expected_version: int,
    ) -> Suite:
        """Append a new immutable revision; never rewrites historical rows."""
        ensure_expected_version(
            entity_type="suite",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        self.require_accepts_new_batch()
        if any(item.id == revision_id for item in self.revisions):
            raise SuiteConflict(suite_id=self.id, reason="revision_id_reused")
        revision = SuiteRevision(
            id=revision_id,
            suite_id=self.id,
            revision_no=len(self.revisions) + 1,
            source_spec_digest=source_spec_digest,
            config_digest=config_digest,
            framework=framework,
            resource_profile_id=resource_profile_id,
            status=SuiteRevisionStatus.APPROVED,
            created_at=created_at,
        )
        return replace(
            self,
            revisions=(*self.revisions, revision),
            current_revision_id=revision_id,
            version=self.version + 1,
        )

    def retire(self, *, retired_at: datetime, expected_version: int) -> Suite:
        """Retire the Suite so new Batch binding is rejected; history remains."""
        ensure_expected_version(
            entity_type="suite",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if self.status is SuiteStatus.RETIRED:
            raise SuiteConflict(suite_id=self.id, reason="suite_already_retired")
        _require_utc("suite", "retired_at", retired_at)
        return replace(
            self,
            status=SuiteStatus.RETIRED,
            retired_at=retired_at,
            version=self.version + 1,
        )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)
