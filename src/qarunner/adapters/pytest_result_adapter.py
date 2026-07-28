"""Adapter loading a sandbox-produced case-results.json (M4, T-M4-PYTEST-ADAPTER-001).

Reads from *results_dir* — the same host-facing results directory
``DockerRunner.run()`` already populates via its existing ``get_archive`` /
``extract_results_archive`` step (see ``docker_runner.py``). This module adds
no new extraction path and no new Docker calls; it only turns the bytes
already on disk into validated domain objects, matching the project's
"control-plane zero execution" boundary: the control plane never parses raw
pytest/junit output, only this small schema-bounded JSON, and only after
locally recomputing its digest rather than trusting anything the sandbox
claims about itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qarunner.domain.digest import Digest
from qarunner.domain.evidence import ArtifactPath, ValidatedCaseSummary
from qarunner.domain.pytest_execution_result import (
    PytestResultAdapterRejected,
    ValidatedCaseResult,
    accept_pytest_execution_result,
)

_RESULTS_FILENAME = "case-results.json"

# Mirrors core/junit.py's existing defensive cap on report-file size: the
# canonical JSON's shape is bounded, but it is still untrusted-process
# output, so a suspiciously huge file must be rejected outright rather than
# fully read into memory.
_MAX_RESULTS_BYTES = 10 * 1024 * 1024


def load_validated_pytest_result(
    results_dir: str, *, exit_code: int
) -> tuple[tuple[ValidatedCaseResult, ...], ValidatedCaseSummary] | None:
    """Load, size-bound, and validate the canonical case-results.json.

    Returns ``None`` only when the file is simply absent — the command
    crashed before ``pytest_sessionfinish`` ever ran — matching
    ``extract_results_archive``'s own "missing artifact is just absent, not
    an error" convention. An oversized or malformed *existing* file is a
    genuine anomaly and is rejected loudly instead, via
    ``PytestResultAdapterRejected``.
    """
    path = Path(results_dir) / _RESULTS_FILENAME
    if not path.is_file():
        return None
    if path.stat().st_size > _MAX_RESULTS_BYTES:
        raise PytestResultAdapterRejected(
            reason=f"case-results.json exceeds {_MAX_RESULTS_BYTES} bytes"
        )

    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise PytestResultAdapterRejected(
            reason=f"case-results.json is not valid JSON: {exc}"
        ) from None
    if not isinstance(payload, dict):
        raise PytestResultAdapterRejected(reason="case-results.json root must be an object")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise PytestResultAdapterRejected(reason="case-results.json 'cases' must be an array")

    digest = Digest(value=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}")
    return accept_pytest_execution_result(
        schema_version=payload.get("schema_version"),
        raw_cases=raw_cases,
        exit_code=exit_code,
        source_artifact_path=ArtifactPath(value=_RESULTS_FILENAME),
        source_artifact_digest=digest,
    )
