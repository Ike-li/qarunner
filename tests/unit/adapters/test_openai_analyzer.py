"""Tests for the OpenAI failure-analyzer adapter (SDK boundary mocked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openai import AsyncOpenAI

from qarunner.adapters.openai_analyzer import OpenAiFailureAnalyzer
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
        "category": "timeout",
        "confidence": "HIGH",
        "summary": "the run timed out",
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
        cases=[TestCaseResult(suite="s", name="t", status="failed", duration_ms=1, message="x")],
        log_tail="",
        baseline_diff=None,
        trend=[],
        flaky_identities=set(),
        allure_report_url=None,
    )
    assert ctx is not None
    return ctx


def _fake_client(create: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _reply(content) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_builds_real_client_by_default():
    analyzer = OpenAiFailureAnalyzer(api_key="test", model="gpt-x")
    assert isinstance(analyzer._client, AsyncOpenAI)


async def test_analyze_parses_reply():
    create = AsyncMock(return_value=_reply(_GOOD))
    analyzer = OpenAiFailureAnalyzer(api_key="k", model="gpt-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    assert diag.category == RootCauseCategory.TIMEOUT
    assert diag.confidence == DiagnosisConfidence.HIGH
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gpt-x"
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"


async def test_analyze_degrades_on_provider_error():
    create = AsyncMock(side_effect=RuntimeError("429 rate limited"))
    analyzer = OpenAiFailureAnalyzer(api_key="k", model="gpt-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    assert diag.category == RootCauseCategory.ENVIRONMENT
    assert diag.confidence == DiagnosisConfidence.LOW


async def test_analyze_degrades_on_none_content():
    create = AsyncMock(return_value=_reply(None))
    analyzer = OpenAiFailureAnalyzer(api_key="k", model="gpt-x", client=_fake_client(create))
    diag = await analyzer.analyze(_context())
    # None content → "" → parse_diagnosis("") → degraded.
    assert diag.category == RootCauseCategory.ENVIRONMENT
