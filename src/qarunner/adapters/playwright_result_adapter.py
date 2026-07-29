"""Adapter loading sandbox-produced Playwright case-results.json (M5).

Same host-facing results_dir contract as the pytest adapter: DockerRunner's
get_archive already populated the directory; this module only size-bounds and
validates the schema-bounded JSON (control plane never parses raw Playwright
junit/HTML/trace tooling).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qarunner.domain.digest import Digest
from qarunner.domain.evidence import ArtifactPath, ValidatedCaseSummary
from qarunner.domain.playwright_execution_result import (
    PlaywrightResultAdapterRejected,
    accept_playwright_execution_result,
)
from qarunner.domain.pytest_execution_result import ValidatedCaseResult

_RESULTS_FILENAME = "case-results.json"
_MAX_RESULTS_BYTES = 10 * 1024 * 1024


def load_validated_playwright_result(
    results_dir: str, *, exit_code: int
) -> tuple[tuple[ValidatedCaseResult, ...], ValidatedCaseSummary] | None:
    """Load and validate Playwright case-results.json; ``None`` if absent."""
    path = Path(results_dir) / _RESULTS_FILENAME
    if not path.is_file():
        return None
    if path.stat().st_size > _MAX_RESULTS_BYTES:
        raise PlaywrightResultAdapterRejected(
            reason=f"case-results.json exceeds {_MAX_RESULTS_BYTES} bytes"
        )

    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise PlaywrightResultAdapterRejected(
            reason=f"case-results.json is not valid JSON: {exc}"
        ) from None
    if not isinstance(payload, dict):
        raise PlaywrightResultAdapterRejected(reason="case-results.json root must be an object")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise PlaywrightResultAdapterRejected(reason="case-results.json 'cases' must be an array")

    digest = Digest(value=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}")
    return accept_playwright_execution_result(
        schema_version=payload.get("schema_version"),
        raw_cases=raw_cases,
        exit_code=exit_code,
        source_artifact_path=ArtifactPath(value=_RESULTS_FILENAME),
        source_artifact_digest=digest,
    )
