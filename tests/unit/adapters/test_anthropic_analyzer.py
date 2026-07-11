"""Tests for the Anthropic failure-analyzer adapter (SDK boundary mocked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from anthropic import AsyncAnthropic

from qarunner.adapters.anthropic_analyzer import AnthropicFailureAnalyzer
from qarunner.core.failure_analysis import FailureContext, build_failure_context
from qarunner.models import (
    DiagnosisConfidence,
    RootCauseCategory,
    Run,
    RunStatus,
    TestCaseResult,
)

_GOOD = json.dumps(
    {
        "category": "assertion",
        "confidence": "MED",
        "summary": "an assertion failed",
        "evidence": [],
        "is_likely_regression": False,
        "next_steps": [],
    }
)


def _context() -> FailureContext:
    run = Run(
        id="r1",
        status=RunStatus.FAILED,
        runner="pytest",
        created_by="alice",
        tests_path="api-tests",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    ctx = build_failure_context(
        run,
        cases=[
            TestCaseResult(suite="s", name="t", status="failed", duration_ms=1, message="boom")
        ],
        log_tail="",
        baseline_diff=None,
        trend=[],
        flaky_identities=set(),
        allure_report_url=None,
    )
    assert ctx is not None
    return ctx


def _fake_client(create: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_builds_real_client_by_default():
    analyzer = AnthropicFailureAnalyzer(api_key="test", model="claude-x")
    assert isinstance(analyzer._client, AsyncAnthropic)


async def test_analyze_parses_reply():
    create = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text=_GOOD)])
    )
    analyzer = AnthropicFailureAnalyzer(api_key="k", model="claude-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    assert diag.category == RootCauseCategory.ASSERTION
    assert diag.confidence == DiagnosisConfidence.MED
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "claude-x"
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["system"]  # system prompt is passed through


async def test_analyze_degrades_on_provider_error():
    create = AsyncMock(side_effect=RuntimeError("network down"))
    analyzer = AnthropicFailureAnalyzer(api_key="k", model="claude-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    assert diag.category == RootCauseCategory.ENVIRONMENT
    assert diag.confidence == DiagnosisConfidence.LOW
    assert "AI provider error" in diag.summary


async def test_analyze_degrades_when_no_text_block():
    create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="tool_use")]))
    analyzer = AnthropicFailureAnalyzer(api_key="k", model="claude-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    # No text block → empty text → parse_diagnosis("") → degraded.
    assert diag.category == RootCauseCategory.ENVIRONMENT


async def test_analyze_degrades_on_malformed_response_shape():
    """BUG: an abnormal response shape (e.g. content-filtered — content=None
    instead of []) raised inside _first_text()/parse_diagnosis(), *outside*
    the try/except around the API call — the "never 500" contract this
    adapter documents only covered the call itself, not reading the reply."""
    create = AsyncMock(return_value=SimpleNamespace(content=None))
    analyzer = AnthropicFailureAnalyzer(api_key="k", model="claude-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    assert diag.category == RootCauseCategory.ENVIRONMENT
    assert diag.confidence == DiagnosisConfidence.LOW
