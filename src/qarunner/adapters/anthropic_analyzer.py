"""Anthropic-backed failure analyzer (provider adapter)."""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic

from qarunner.core.failure_analysis import (
    FailureContext,
    build_messages,
    degraded_diagnosis,
    parse_diagnosis,
)
from qarunner.models import FailureDiagnosis

logger = logging.getLogger(__name__)


def _first_text(content: list) -> str:
    """Return the first text block's text from an Anthropic message, or ''."""
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


class AnthropicFailureAnalyzer:
    """Produce a structured diagnosis via the Anthropic Messages API.

    ``client`` is injectable for tests; in production it is constructed from the
    configured api_key/base_url/timeout. Prompt building and reply parsing live
    in ``core.failure_analysis`` so the result is provider-agnostic.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout: float = 60.0,
        max_tokens: int = 4096,
        *,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._client = client or AsyncAnthropic(
            api_key=api_key, base_url=base_url or None, timeout=timeout
        )
        self._model = model
        self._max_tokens = max_tokens

    async def analyze(self, context: FailureContext) -> FailureDiagnosis:
        system, user = build_messages(context)
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 — provider errors degrade, never 500
            logger.warning("Anthropic diagnosis failed", exc_info=True)
            return degraded_diagnosis(f"AI provider error: {exc}")
        return parse_diagnosis(_first_text(resp.content))
