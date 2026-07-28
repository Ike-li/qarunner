"""Canonical per-case pytest execution results (M4, T-M4-PYTEST-ADAPTER-001).

Turns the sandbox-produced, schema-bounded ``case-results.json`` into
validated per-case results plus the existing aggregate ``ValidatedCaseSummary``
— the only form the control plane is ever allowed to parse. Raw junit.xml/
pytest output stays opaque archival content; it is parsed only inside the
sandbox, by the ``qarunner_canonical_plugin`` pytest plugin baked into the
executor image, never by the control plane (T-M4-PYTEST-ADAPTER-001's
"控制面零执行").
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qarunner.domain.digest import Digest
from qarunner.domain.errors import ArtifactValidationError, DomainValidationError
from qarunner.domain.evidence import (
    ArtifactClass,
    ArtifactPath,
    PlatformExitClass,
    ValidatedCaseSummary,
    VerifiedArtifact,
)
from qarunner.domain.manifest import FrameworkLocator

CASE_RESULT_SCHEMA_VERSION = "qep.pytest-case-result.v1"

# pytest.ExitCode.NO_TESTS_COLLECTED — the one legitimate reason a real M4
# Attempt (which runs one whole representative suite, not a shard) reports
# zero cases. Any other empty-case-list exit code means collection itself
# blew up (e.g. an import error), which never reaches pytest_runtest_logreport
# at all and must not be silently treated as "nothing to report."
_NO_TESTS_COLLECTED_EXIT_CODE = 5


class CaseOutcome(enum.StrEnum):
    """Canonical per-case outcome — mirrors pytest's own vocabulary.

    ERROR is a setup/teardown-phase failure (pytest's own `E` vs `f`
    distinction in `-r` short-summary codes), distinct from a call-phase
    assertion FAILED. xfail/xpass fold into SKIPPED/PASSED respectively as a
    documented v1 simplification (see qarunner_canonical_plugin).
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


def pytest_nodeid_locator(nodeid: str) -> FrameworkLocator:
    """The one shared file/node split for ``pytest_nodeid`` locators.

    Partitions on the FIRST ``::`` only (not ``split``), so a parametrize id
    that itself contains ``::`` stays intact inside the node part instead of
    being torn apart. This is the single utility any pytest_nodeid-locator
    caller (collection or execution-result adapter) must share — two
    independent re-splits could disagree on an edge case and break cross-stage
    Case identity.
    """
    if not isinstance(nodeid, str) or not nodeid.strip():
        raise DomainValidationError(entity_type="pytest_nodeid", field="nodeid", reason="invalid")
    file_part, sep, node_part = nodeid.partition("::")
    return FrameworkLocator(
        schema_version="qep.pytest-locator.v1",
        kind="pytest_nodeid",
        parts=(("file", file_part), ("node", node_part if sep else nodeid)),
    )


@dataclass(frozen=True, slots=True)
class ValidatedCaseResult:
    """One schema-checked per-case outcome from a real pytest Attempt."""

    stable_case_id: str
    framework_locator: FrameworkLocator
    outcome: CaseOutcome
    duration_ms: int
    message: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.stable_case_id, str) or not self.stable_case_id.strip():
            raise DomainValidationError(
                entity_type="validated_case_result", field="stable_case_id", reason="invalid"
            )
        if not isinstance(self.framework_locator, FrameworkLocator):
            raise DomainValidationError(
                entity_type="validated_case_result",
                field="framework_locator",
                reason="not_framework_locator",
            )
        if not isinstance(self.outcome, CaseOutcome):
            raise DomainValidationError(
                entity_type="validated_case_result", field="outcome", reason="not_case_outcome"
            )
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise DomainValidationError(
                entity_type="validated_case_result", field="duration_ms", reason="invalid"
            )
        if self.message is not None and not isinstance(self.message, str):
            raise DomainValidationError(
                entity_type="validated_case_result", field="message", reason="not_string"
            )


class PytestResultAdapterRejected(ValueError):
    """Raised when a sandbox-produced case-results.json payload is malformed."""

    code = "pytest_result_adapter_rejected"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"pytest result adapter output rejected: {reason}")


def accept_pytest_execution_result(
    *,
    schema_version: str,
    raw_cases: Sequence[Mapping[str, object]],
    exit_code: int,
    source_artifact_path: ArtifactPath,
    source_artifact_digest: Digest,
) -> tuple[tuple[ValidatedCaseResult, ...], ValidatedCaseSummary]:
    """Validate and canonicalize the sandbox-produced ``case-results.json``.

    Rejects: an unrecognized ``schema_version``; a malformed case entry;
    duplicate ``stable_case_id`` (the plugin's own fold step must already
    de-dupe reruns before this ever sees the list — a true invariant here, not
    a case that legitimately fires); an invalid outcome value; a negative
    duration; an empty case list unless *exit_code* is
    ``NO_TESTS_COLLECTED`` (5). Builds the existing ``ValidatedCaseSummary``
    aggregate (ERROR folds into the ``failed`` bucket) so the result slots
    into ``build_evidence_manifest``/``EvidenceVerifier`` unchanged.
    """
    if schema_version != CASE_RESULT_SCHEMA_VERSION:
        raise PytestResultAdapterRejected(reason=f"unsupported schema_version {schema_version!r}")

    if not raw_cases:
        if exit_code != _NO_TESTS_COLLECTED_EXIT_CODE:
            raise PytestResultAdapterRejected(
                reason="empty case list without a NO_TESTS_COLLECTED exit code"
            )
        return (), _summary(source_artifact_path, source_artifact_digest, 0, 0, 0, 0)

    seen_ids: set[str] = set()
    cases: list[ValidatedCaseResult] = []
    passed = failed = skipped = 0
    for raw in raw_cases:
        case = _parse_raw_case(raw)
        if case.stable_case_id in seen_ids:
            raise PytestResultAdapterRejected(
                reason=f"duplicate stable_case_id {case.stable_case_id!r}"
            )
        seen_ids.add(case.stable_case_id)
        cases.append(case)
        if case.outcome is CaseOutcome.PASSED:
            passed += 1
        elif case.outcome is CaseOutcome.SKIPPED:
            skipped += 1
        else:  # FAILED and ERROR both count toward the aggregate's failed bucket
            failed += 1

    summary = _summary(
        source_artifact_path, source_artifact_digest, len(cases), passed, failed, skipped
    )
    return tuple(cases), summary


def _summary(
    source_artifact_path: ArtifactPath,
    source_artifact_digest: Digest,
    expected: int,
    passed: int,
    failed: int,
    skipped: int,
) -> ValidatedCaseSummary:
    return ValidatedCaseSummary(
        schema_version=CASE_RESULT_SCHEMA_VERSION,
        source_artifact_path=source_artifact_path,
        source_artifact_digest=source_artifact_digest,
        expected=expected,
        passed=passed,
        failed=failed,
        skipped=skipped,
        not_reported=0,
        unexpected=0,
    )


def _parse_raw_case(raw: Mapping[str, object]) -> ValidatedCaseResult:
    try:
        stable_case_id = raw["stable_case_id"]
        outcome_value = raw["outcome"]
        duration_ms = raw["duration_ms"]
        message = raw.get("message")
    except (KeyError, TypeError) as exc:
        raise PytestResultAdapterRejected(reason=f"malformed case entry: {exc}") from None
    try:
        outcome = CaseOutcome(outcome_value)
    except ValueError:
        raise PytestResultAdapterRejected(reason=f"invalid outcome {outcome_value!r}") from None
    if not isinstance(stable_case_id, str) or not stable_case_id.strip():
        raise PytestResultAdapterRejected(reason="stable_case_id must be a non-empty string")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise PytestResultAdapterRejected(reason="duration_ms must be a non-negative integer")
    if message is not None and not isinstance(message, str):
        raise PytestResultAdapterRejected(reason="message must be a string or null")
    return ValidatedCaseResult(
        stable_case_id=stable_case_id,
        framework_locator=pytest_nodeid_locator(stable_case_id),
        outcome=outcome,
        duration_ms=duration_ms,
        message=message,
    )


@dataclass(frozen=True, slots=True)
class DeclaredArtifact:
    """Sandbox-claimed Artifact metadata — untrusted until locally reverified.

    Mirrors the "declared then server-recomputed" security property the
    project's real (not-yet-built, multi-host) Worker Upload Session protocol
    describes, without needing that network protocol: for this same-host
    vertical slice, "reverified" means recomputed from the bytes actually read
    back off disk after ``get_archive``.
    """

    path: ArtifactPath
    content_class: ArtifactClass
    size_bytes: int
    digest: Digest

    def __post_init__(self) -> None:
        if not isinstance(self.path, ArtifactPath):
            raise ArtifactValidationError(field="path", reason="not_artifact_path")
        if not isinstance(self.content_class, ArtifactClass):
            raise ArtifactValidationError(field="content_class", reason="not_artifact_class")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ArtifactValidationError(field="size_bytes", reason="must be non-negative")
        if not isinstance(self.digest, Digest):
            raise ArtifactValidationError(field="digest", reason="not_digest")


def verify_declared_artifact(
    declared: DeclaredArtifact, *, recomputed_digest: Digest, recomputed_size: int
) -> VerifiedArtifact:
    """Promote a declared Artifact to verified only if a locally recomputed
    digest/size (from the bytes actually received) match what was declared."""
    if not isinstance(declared, DeclaredArtifact):
        raise ArtifactValidationError(field="declared", reason="not_declared_artifact")
    if declared.digest != recomputed_digest or declared.size_bytes != recomputed_size:
        raise ArtifactValidationError(field="digest", reason="declared_mismatch_recomputed")
    return VerifiedArtifact(
        path=declared.path,
        content_class=declared.content_class,
        size_bytes=recomputed_size,
        digest=recomputed_digest,
    )


# ── T-M4-RESULT-001: Attempt-level classification at the Worker boundary ─────


class PytestAttemptResultClass(enum.StrEnum):
    """Worker-boundary classification of one pytest Attempt.

    Distinct from per-case :class:`CaseOutcome` and from orchestration phase.
    Maps onto EvidenceOutcome / AttemptState terminal facts:

    - ``passed`` / ``test_failed`` → platform exit COMPLETED + Evidence outcome
    - ``infra_failed`` → platform exit INFRA_FAILED (dominates case content)
    - ``cancelled`` → platform exit CANCELLED (needs a separate stop proof at
      Evidence finalize; this class only records the cancel *signal*)
    - ``unknown`` → cannot produce a terminal Evidence outcome; control plane
      must enter the unknown-observation path instead of finalizing
    """

    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PytestAttemptResult:
    """Result of :func:`classify_pytest_attempt_result`."""

    result_class: PytestAttemptResultClass
    platform_exit_class: PlatformExitClass
    exit_code: int | None
    timed_out: bool
    oom: bool
    cancelled: bool
    requires_unknown_observation: bool

    @property
    def timeout(self) -> bool:
        """Alias matching :class:`TrustedExitFacts.timeout` naming."""
        return self.timed_out


def classify_pytest_attempt_result(
    *,
    exit_code: int | None,
    timed_out: bool,
    cancelled: bool,
    case_summary: ValidatedCaseSummary | None,
    oom: bool = False,
) -> PytestAttemptResult:
    """Classify one pytest Attempt from platform process facts + case summary.

    Priority (highest first), kept intentionally parallel to
    ``evidence._classify_outcome``:

    1. ``cancelled`` → CANCELLED (dominates timeout/case content)
    2. ``timed_out`` / ``oom`` → INFRA_FAILED (dominates case content)
    3. missing ``exit_code`` → UNKNOWN (no observed process end)
    4. missing ``case_summary`` → INFRA_FAILED (plugin never produced results)
    5. incomplete summary (``not_reported``/``unexpected``) → UNKNOWN
    6. exit 0 + zero failures → PASSED
    7. nonzero exit + failures > 0 → TEST_FAILED
    8. nonzero exit + zero failures → INFRA_FAILED (pytest infra/collection)
    9. exit 0 + failures > 0 → UNKNOWN (process/summary contradiction)
    """
    entity = "pytest_attempt_result"
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise DomainValidationError(entity_type=entity, field="exit_code", reason="invalid")
    if not isinstance(timed_out, bool):
        raise DomainValidationError(entity_type=entity, field="timed_out", reason="not_bool")
    if not isinstance(cancelled, bool):
        raise DomainValidationError(entity_type=entity, field="cancelled", reason="not_bool")
    if not isinstance(oom, bool):
        raise DomainValidationError(entity_type=entity, field="oom", reason="not_bool")
    if case_summary is not None and not isinstance(case_summary, ValidatedCaseSummary):
        raise DomainValidationError(
            entity_type=entity, field="case_summary", reason="not_validated_case_summary"
        )

    if cancelled:
        return _result(
            PytestAttemptResultClass.CANCELLED,
            PlatformExitClass.CANCELLED,
            exit_code=exit_code,
            timed_out=timed_out,
            oom=oom,
            cancelled=True,
            unknown=False,
        )
    if timed_out or oom:
        return _result(
            PytestAttemptResultClass.INFRA_FAILED,
            PlatformExitClass.INFRA_FAILED,
            exit_code=exit_code,
            timed_out=timed_out,
            oom=oom,
            cancelled=False,
            unknown=False,
        )
    if exit_code is None:
        return _result(
            PytestAttemptResultClass.UNKNOWN,
            PlatformExitClass.INFRA_FAILED,
            exit_code=None,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=True,
        )
    if case_summary is None:
        return _result(
            PytestAttemptResultClass.INFRA_FAILED,
            PlatformExitClass.INFRA_FAILED,
            exit_code=exit_code,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=False,
        )
    incomplete = case_summary.not_reported > 0 or case_summary.unexpected > 0
    if incomplete:
        return _result(
            PytestAttemptResultClass.UNKNOWN,
            PlatformExitClass.INFRA_FAILED,
            exit_code=exit_code,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=True,
        )
    if exit_code == 0 and case_summary.failed == 0:
        return _result(
            PytestAttemptResultClass.PASSED,
            PlatformExitClass.COMPLETED,
            exit_code=exit_code,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=False,
        )
    if exit_code != 0 and case_summary.failed > 0:
        return _result(
            PytestAttemptResultClass.TEST_FAILED,
            PlatformExitClass.COMPLETED,
            exit_code=exit_code,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=False,
        )
    if exit_code != 0 and case_summary.failed == 0:
        return _result(
            PytestAttemptResultClass.INFRA_FAILED,
            PlatformExitClass.INFRA_FAILED,
            exit_code=exit_code,
            timed_out=False,
            oom=False,
            cancelled=False,
            unknown=False,
        )
    # exit_code == 0 and case_summary.failed > 0
    return _result(
        PytestAttemptResultClass.UNKNOWN,
        PlatformExitClass.INFRA_FAILED,
        exit_code=exit_code,
        timed_out=False,
        oom=False,
        cancelled=False,
        unknown=True,
    )


def _result(
    result_class: PytestAttemptResultClass,
    platform_exit_class: PlatformExitClass,
    *,
    exit_code: int | None,
    timed_out: bool,
    oom: bool,
    cancelled: bool,
    unknown: bool,
) -> PytestAttemptResult:
    return PytestAttemptResult(
        result_class=result_class,
        platform_exit_class=platform_exit_class,
        exit_code=exit_code,
        timed_out=timed_out,
        oom=oom,
        cancelled=cancelled,
        requires_unknown_observation=unknown,
    )
