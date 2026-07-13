"""M0 Evidence value-object and deterministic-manifest contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    ArtifactClass,
    ArtifactPath,
    ArtifactValidationError,
    Digest,
    EvidenceManifest,
    EvidenceNotReady,
    PlatformExitClass,
    TrustedCancellationStop,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    WorkerRef,
    build_evidence_manifest,
    canonical_digest,
)


def _digest(label: str) -> Digest:
    return canonical_digest(
        schema_version="qep.artifact-content.v1",
        payload={"label": label},
    )


def _artifact(
    path: str,
    *,
    content_class: ArtifactClass,
    size_bytes: int = 16,
    digest: Digest | None = None,
) -> VerifiedArtifact:
    return VerifiedArtifact(
        path=ArtifactPath(path),
        content_class=content_class,
        size_bytes=size_bytes,
        digest=digest if digest is not None else _digest(path),
    )


def _passing_summary(
    artifact: VerifiedArtifact,
    *,
    source_path: ArtifactPath | None = None,
    source_digest: Digest | None = None,
) -> ValidatedCaseSummary:
    return ValidatedCaseSummary(
        schema_version="qep.case-summary.v1",
        source_artifact_path=source_path if source_path is not None else artifact.path,
        source_artifact_digest=(source_digest if source_digest is not None else artifact.digest),
        expected=1,
        passed=1,
        failed=0,
        skipped=0,
        not_reported=0,
        unexpected=0,
    )


def _build_manifest(
    *,
    artifacts: tuple[VerifiedArtifact, ...],
    case_summary: ValidatedCaseSummary | None,
    trusted_exit: TrustedExitFacts | None = None,
    cancellation_stop: TrustedCancellationStop | None = None,
):
    return build_evidence_manifest(
        attempt_id="attempt-001",
        run_id="run-001",
        attempt_no=1,
        assignment_id="assignment-001",
        fence=1,
        worker=WorkerRef(worker_id="worker-001", generation=1),
        execution_spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"suite_revision": "suite@abc123"},
        ),
        trusted_exit=(
            TrustedExitFacts(
                source_event_id="event-exit-001",
                pid=731,
                exit_class=PlatformExitClass.COMPLETED,
                exit_code=0,
                signal=None,
                oom=False,
                timeout=False,
            )
            if trusted_exit is None
            else trusted_exit
        ),
        case_summary=case_summary,
        artifacts=artifacts,
        cancellation_stop=cancellation_stop,
    )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        pytest.param("\ud800", id="invalid-utf8-surrogate"),
        pytest.param("", id="empty"),
        pytest.param("/results/output.json", id="absolute"),
        pytest.param("results/output\x00.json", id="nul"),
        pytest.param(r"results\output.json", id="backslash"),
        pytest.param("./results.json", id="leading-dot-segment"),
        pytest.param("results/./output.json", id="nested-dot-segment"),
        pytest.param("../results.json", id="leading-dot-dot-segment"),
        pytest.param("results/../output.json", id="nested-dot-dot-segment"),
        pytest.param("results//output.json", id="empty-middle-segment"),
        pytest.param("results/", id="empty-final-segment"),
    ],
)
def test_artifact_path_rejects_unsafe_or_noncanonical_values(unsafe_path: str) -> None:
    with pytest.raises(ArtifactValidationError) as caught:
        ArtifactPath(unsafe_path)

    assert caught.value.code == "artifact_invalid"
    assert caught.value.field == "path"


def test_verified_artifact_rejects_negative_size() -> None:
    with pytest.raises(ArtifactValidationError) as caught:
        _artifact(
            "logs/output.txt",
            content_class=ArtifactClass.LOG,
            size_bytes=-1,
        )

    assert caught.value.code == "artifact_invalid"
    assert caught.value.field == "size_bytes"


def test_verified_artifact_requires_a_digest_value_object() -> None:
    with pytest.raises(ArtifactValidationError) as caught:
        VerifiedArtifact(
            path=ArtifactPath("logs/output.txt"),
            content_class=ArtifactClass.LOG,
            size_bytes=10,
            digest="sha256:not-a-value-object",  # type: ignore[arg-type]
        )

    assert caught.value.code == "artifact_invalid"
    assert caught.value.field == "digest"


def test_manifest_rejects_duplicate_artifact_paths() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    first_duplicate = _artifact(
        "logs/output.txt",
        content_class=ArtifactClass.LOG,
        digest=_digest("first-copy"),
    )
    second_duplicate = _artifact(
        "logs/output.txt",
        content_class=ArtifactClass.DIAGNOSTIC,
        digest=_digest("second-copy"),
    )

    with pytest.raises(ArtifactValidationError) as caught:
        _build_manifest(
            artifacts=(result, first_duplicate, second_duplicate),
            case_summary=_passing_summary(result),
        )

    assert caught.value.code == "artifact_invalid"
    assert caught.value.field == "path"
    assert caught.value.reason == "must be unique within an Attempt"


def test_artifact_input_order_does_not_change_manifest_root() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    log = _artifact("logs/output.txt", content_class=ArtifactClass.LOG)
    summary = _passing_summary(result)

    forward = _build_manifest(artifacts=(result, log), case_summary=summary)
    reverse = _build_manifest(artifacts=(log, result), case_summary=summary)

    assert forward.root_digest == reverse.root_digest
    assert forward.artifacts == reverse.artifacts


def test_v1_evidence_manifest_constructor_keeps_cancellation_stop_optional() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    manifest = _build_manifest(
        artifacts=(result,),
        case_summary=_passing_summary(result),
    )
    legacy_fields = {
        field: getattr(manifest, field)
        for field in (
            "schema_version",
            "attempt_id",
            "run_id",
            "attempt_no",
            "assignment_id",
            "fence",
            "worker",
            "execution_spec_digest",
            "platform_exit",
            "case_summary",
            "artifacts",
            "classification_version",
            "outcome",
            "root_digest",
        )
    }

    restored = EvidenceManifest(**legacy_fields)

    assert restored == manifest
    assert restored.schema_version == "qep.m0-attempt-evidence.v1"
    assert restored.classification_version == "qep.attempt-classification.v1"
    assert restored.cancellation_stop is None


@pytest.mark.parametrize(
    ("field", "replacement_value"),
    [
        pytest.param("path", ArtifactPath("logs/renamed.txt"), id="path"),
        pytest.param(
            "content_class",
            ArtifactClass.DIAGNOSTIC,
            id="content-class",
        ),
        pytest.param("size_bytes", 17, id="size"),
        pytest.param("digest", _digest("changed-content"), id="digest"),
    ],
)
def test_changing_any_artifact_metadata_changes_manifest_root(
    field: str,
    replacement_value: object,
) -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    log = _artifact("logs/output.txt", content_class=ArtifactClass.LOG)
    summary = _passing_summary(result)
    baseline = _build_manifest(artifacts=(result, log), case_summary=summary)

    changed_log = replace(log, **{field: replacement_value})
    changed = _build_manifest(artifacts=(result, changed_log), case_summary=summary)

    assert changed.root_digest != baseline.root_digest


def test_manifest_rejects_missing_case_summary_source_artifact() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    summary = _passing_summary(
        result,
        source_path=ArtifactPath("missing-results.json"),
    )

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(artifacts=(result,), case_summary=summary)

    assert caught.value.code == "evidence_not_ready"
    assert caught.value.reason == "case summary source artifact is missing"


def test_manifest_rejects_case_summary_source_with_wrong_class() -> None:
    log = _artifact("case-results.json", content_class=ArtifactClass.LOG)

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(artifacts=(log,), case_summary=_passing_summary(log))

    assert caught.value.code == "evidence_not_ready"
    assert caught.value.reason == "case summary source is not a structured result"


def test_manifest_rejects_case_summary_source_with_wrong_digest() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    summary = _passing_summary(result, source_digest=_digest("other-results"))

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(artifacts=(result,), case_summary=summary)

    assert caught.value.code == "evidence_not_ready"
    assert caught.value.reason == "case summary source digest does not match the artifact"


@pytest.mark.parametrize(
    "negative_field",
    ["expected", "passed", "failed", "skipped", "not_reported", "unexpected"],
)
def test_case_summary_rejects_negative_counts(negative_field: str) -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    counts = {
        "expected": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "not_reported": 0,
        "unexpected": 0,
    }
    counts[negative_field] = -1

    with pytest.raises(EvidenceNotReady) as caught:
        ValidatedCaseSummary(
            schema_version="qep.case-summary.v1",
            source_artifact_path=result.path,
            source_artifact_digest=result.digest,
            **counts,
        )

    assert caught.value.code == "evidence_not_ready"
    assert caught.value.reason == "case summary counts must be non-negative"


def test_case_summary_rejects_counts_that_do_not_account_for_expected_cases() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        ValidatedCaseSummary(
            schema_version="qep.case-summary.v1",
            source_artifact_path=result.path,
            source_artifact_digest=result.digest,
            expected=2,
            passed=1,
            failed=0,
            skipped=0,
            not_reported=0,
            unexpected=0,
        )

    assert caught.value.code == "evidence_not_ready"
    assert caught.value.reason == "case summary does not account for every expected case"


def test_manifest_rejects_cancelled_exit_without_cancellation_stop_proof() -> None:
    cancelled = TrustedExitFacts(
        source_event_id="event-cancelled-001",
        pid=None,
        exit_class=PlatformExitClass.CANCELLED,
        exit_code=None,
        signal=None,
        oom=False,
        timeout=False,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(
            artifacts=(),
            case_summary=None,
            trusted_exit=cancelled,
        )

    assert caught.value.reason == "trusted cancellation stop proof is missing"


def test_manifest_rejects_non_stop_value_for_cancelled_exit_stably() -> None:
    cancelled = TrustedExitFacts(
        source_event_id="event-cancelled-001",
        pid=731,
        exit_class=PlatformExitClass.CANCELLED,
        exit_code=None,
        signal=15,
        oom=False,
        timeout=False,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(
            artifacts=(),
            case_summary=None,
            trusted_exit=cancelled,
            cancellation_stop="bad",  # type: ignore[arg-type]
        )

    assert caught.value.reason == "trusted cancellation stop proof is invalid"


def test_cancelled_manifest_binds_trusted_stop_proof_in_a_new_schema() -> None:
    worker = WorkerRef(worker_id="worker-001", generation=1)
    intent_digest = _digest("cancellation-intent")
    cancelled = TrustedExitFacts(
        source_event_id="event-cancelled-001",
        pid=731,
        exit_class=PlatformExitClass.CANCELLED,
        exit_code=None,
        signal=15,
        oom=False,
        timeout=False,
    )
    stop = TrustedCancellationStop(
        run_id="run-001",
        attempt_id="attempt-001",
        fence=1,
        worker=worker,
        cancellation_intent_digest=intent_digest,
        source_event_id=cancelled.source_event_id,
        process_stopped_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
        sut_access_stopped_at=datetime(2026, 7, 14, 0, 2, 30, tzinfo=UTC),
        recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )
    changed_stop = replace(
        stop,
        sut_access_stopped_at=stop.sut_access_stopped_at + timedelta(seconds=1),
    )

    manifest = _build_manifest(
        artifacts=(),
        case_summary=None,
        trusted_exit=cancelled,
        cancellation_stop=stop,
    )
    changed_manifest = _build_manifest(
        artifacts=(),
        case_summary=None,
        trusted_exit=cancelled,
        cancellation_stop=changed_stop,
    )

    assert manifest.schema_version == "qep.m0-attempt-evidence.v2"
    assert manifest.classification_version == "qep.attempt-classification.v1"
    assert manifest.outcome.value == "cancelled"
    assert manifest.cancellation_stop is stop
    assert manifest.root_digest != changed_manifest.root_digest


def test_manifest_rejects_contradictory_cancellation_stop_binding() -> None:
    worker = WorkerRef(worker_id="worker-001", generation=1)
    cancelled = TrustedExitFacts(
        source_event_id="event-cancelled-001",
        pid=731,
        exit_class=PlatformExitClass.CANCELLED,
        exit_code=None,
        signal=15,
        oom=False,
        timeout=False,
    )
    stop = TrustedCancellationStop(
        run_id="run-001",
        attempt_id="attempt-001",
        fence=1,
        worker=worker,
        cancellation_intent_digest=_digest("cancellation-intent"),
        source_event_id=cancelled.source_event_id,
        process_stopped_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
        sut_access_stopped_at=datetime(2026, 7, 14, 0, 2, 30, tzinfo=UTC),
        recorded_at=datetime(2026, 7, 14, 0, 3, tzinfo=UTC),
    )
    completed = replace(
        cancelled,
        source_event_id="event-completed-001",
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=0,
        signal=None,
    )
    cases = (
        (completed, stop, "trusted cancellation stop proof requires a cancelled exit"),
        (
            cancelled,
            replace(stop, source_event_id="event-other-001"),
            "cancellation stop source event does not match exit facts",
        ),
        (
            cancelled,
            replace(stop, attempt_id="attempt-other"),
            "cancellation stop proof does not match the attempt",
        ),
    )

    for trusted_exit, cancellation_stop, reason in cases:
        with pytest.raises(EvidenceNotReady) as caught:
            _build_manifest(
                artifacts=(),
                case_summary=None,
                trusted_exit=trusted_exit,
                cancellation_stop=cancellation_stop,
            )
        assert caught.value.reason == reason


def test_manifest_classifies_completed_failed_cases_as_test_failed() -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    summary = replace(_passing_summary(result), passed=0, failed=1)
    failed_exit = TrustedExitFacts(
        source_event_id="event-exit-001",
        pid=731,
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=1,
        signal=None,
        oom=False,
        timeout=False,
    )

    manifest = _build_manifest(
        artifacts=(result,),
        case_summary=summary,
        trusted_exit=failed_exit,
    )

    assert manifest.outcome.value == "test_failed"


@pytest.mark.parametrize(
    ("exit_changes", "summary_changes", "omit_summary", "reason"),
    [
        pytest.param(
            {"pid": None},
            {},
            False,
            "completed process facts require a positive pid",
            id="missing-pid",
        ),
        pytest.param(
            {"signal": 9},
            {},
            False,
            "completed process facts contradict a platform failure",
            id="signal",
        ),
        pytest.param(
            {"oom": True},
            {},
            False,
            "completed process facts contradict a platform failure",
            id="oom",
        ),
        pytest.param(
            {"timeout": True},
            {},
            False,
            "completed process facts contradict a platform failure",
            id="timeout",
        ),
        pytest.param(
            {"exit_code": None},
            {},
            False,
            "completed process facts require an exit code",
            id="missing-exit-code",
        ),
        pytest.param(
            {},
            {},
            True,
            "completed process facts require a validated case summary",
            id="missing-case-summary",
        ),
        pytest.param(
            {},
            {"passed": 0, "failed": 1},
            False,
            "process exit and validated case summary are contradictory",
            id="zero-exit-with-failed-case",
        ),
        pytest.param(
            {"exit_code": 1},
            {},
            False,
            "process exit and validated case summary are contradictory",
            id="nonzero-exit-with-passing-case",
        ),
        pytest.param(
            {},
            {"passed": 0, "not_reported": 1},
            False,
            "process exit and validated case summary are contradictory",
            id="not-reported",
        ),
        pytest.param(
            {},
            {"unexpected": 1},
            False,
            "process exit and validated case summary are contradictory",
            id="unexpected",
        ),
    ],
)
def test_manifest_rejects_incomplete_or_contradictory_completed_facts(
    exit_changes: dict[str, object],
    summary_changes: dict[str, int],
    omit_summary: bool,
    reason: str,
) -> None:
    result = _artifact(
        "case-results.json",
        content_class=ArtifactClass.STRUCTURED_RESULT,
    )
    trusted_exit = TrustedExitFacts(
        source_event_id="event-exit-001",
        pid=731,
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=0,
        signal=None,
        oom=False,
        timeout=False,
    )
    summary = replace(_passing_summary(result), **summary_changes)

    with pytest.raises(EvidenceNotReady) as caught:
        _build_manifest(
            artifacts=(result,),
            case_summary=None if omit_summary else summary,
            trusted_exit=replace(trusted_exit, **exit_changes),
        )

    assert caught.value.reason == reason
