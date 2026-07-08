"""AI failure-diagnosis port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.core.failure_analysis import FailureContext
from qarunner.models import FailureDiagnosis


@runtime_checkable
class FailureAnalyzer(Protocol):
    """Port for producing a structured diagnosis from an assembled context.

    Implementations (one per LLM provider) call ``build_messages`` /
    ``parse_diagnosis`` from ``core.failure_analysis`` so the diagnosis is
    provider-agnostic — the port hides which SDK produced it.
    """

    async def analyze(self, context: FailureContext) -> FailureDiagnosis: ...
