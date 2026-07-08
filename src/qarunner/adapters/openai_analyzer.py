"""OpenAI-backed failure analyzer (provider adapter)."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from qarunner.core.failure_analysis import (
    FailureContext,
    build_messages,
    degraded_diagnosis,
    parse_diagnosis,
)
from qarunner.models import FailureDiagnosis

logger = logging.getLogger(__name__)


class OpenAiFailureAnalyzer:
    """Produce a structured diagnosis via the OpenAI Chat Completions API.

    ``client`` is injectable for tests; in production it is constructed from the
    configured api_key/base_url/timeout. ``base_url`` lets it target any
    OpenAI-compatible endpoint. Prompt building and reply parsing live in
    ``core.failure_analysis`` so the result is provider-agnostic.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout: float = 60.0,
        max_tokens: int = 4096,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url or None, timeout=timeout
        )
        self._model = model
        self._max_tokens = max_tokens

    async def analyze(self, context: FailureContext) -> FailureDiagnosis:
        system, user = build_messages(context)
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001 — provider errors degrade, never 500
            logger.warning("OpenAI diagnosis failed", exc_info=True)
            return degraded_diagnosis(f"AI provider error: {exc}")
        return parse_diagnosis(resp.choices[0].message.content or "")
