"""Tests for core.notification — Feishu card builder and sender."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from qarunner.core.notification import build_run_card, send_feishu_card
from qarunner.models import Run, RunStatus, TestSummary


def _make_run(status: RunStatus, summary: TestSummary | None = None) -> Run:
    return Run(
        id="run-abc123",
        status=status,
        runner="pytest",
        created_by="testuser",
        tests_path="my_suite",
        allure_enabled=True,
        created_at=datetime(2026, 6, 30, 10, 0, 0, tzinfo=UTC),
        summary=summary,
    )


def _make_summary(
    passed: int = 8,
    failed: int = 2,
    skipped: int = 1,
    error: int = 0,
    total: int = 11,
    duration_ms: int = 5000,
) -> TestSummary:
    return TestSummary(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        error=error,
        duration_ms=duration_ms,
    )


# ── build_run_card ────────────────────────────────────────────────────────


def test_card_structure_has_required_fields() -> None:
    """The card dict has the msg_type and card envelope Feishu expects."""
    run = _make_run(RunStatus.COMPLETED, _make_summary())
    card = build_run_card(run, "https://qa.example.com/runs/run-abc123/report")

    assert card["msg_type"] == "interactive"
    assert "card" in card
    assert "header" in card["card"]
    assert "elements" in card["card"]


def test_card_header_shows_pass_rate() -> None:
    """Header title includes run ID prefix and pass percentage."""
    run = _make_run(RunStatus.COMPLETED, _make_summary(passed=8, total=10))
    card = build_run_card(run, "https://qa.example.com/runs/run-abc123/report")

    title = card["card"]["header"]["title"]["content"]
    assert "80.0%" in title
    assert "run-abc1" in title  # run ID truncated to 8 chars


def test_card_color_green_for_completed() -> None:
    run = _make_run(RunStatus.COMPLETED, _make_summary())
    card = build_run_card(run, "https://qa.example.com/report")

    assert card["card"]["header"]["template"] == "green"


def test_card_color_red_for_failed() -> None:
    run = _make_run(RunStatus.FAILED)
    card = build_run_card(run, "https://qa.example.com/report")

    assert card["card"]["header"]["template"] == "red"


def test_card_color_orange_for_timeout() -> None:
    run = _make_run(RunStatus.TIMEOUT)
    card = build_run_card(run, "https://qa.example.com/report")

    assert card["card"]["header"]["template"] == "orange"


def test_card_color_yellow_for_cancelled() -> None:
    run = _make_run(RunStatus.CANCELLED)
    card = build_run_card(run, "https://qa.example.com/report")

    assert card["card"]["header"]["template"] == "yellow"


def test_card_includes_statistics() -> None:
    run = _make_run(
        RunStatus.COMPLETED,
        _make_summary(
            passed=8,
            failed=2,
            skipped=1,
            error=0,
            total=11,
            duration_ms=5000,
        ),
    )
    card = build_run_card(run, "https://qa.example.com/report")

    # Find the text content across all elements
    all_text = str(card["card"]["elements"])
    assert "8" in all_text
    assert "2" in all_text
    assert "72.7%" in all_text  # 8/11


def test_card_contains_allure_button_with_url() -> None:
    allure_url = "https://qa.example.com/runs/run-abc123/report"
    run = _make_run(RunStatus.COMPLETED, _make_summary())
    card = build_run_card(run, allure_url)

    # The elements should contain a button/link action with the allure URL
    elements = card["card"]["elements"]
    actions_found = False
    for el in elements:
        if "actions" in el:
            for action in el["actions"]:
                if "url" in action and action["url"] == allure_url:
                    actions_found = True
    assert actions_found, "Allure report button not found in card"


def test_card_with_zero_total_shows_na_rates() -> None:
    """When total==0, per-status lines show N/A instead of a percentage."""
    run = _make_run(
        RunStatus.COMPLETED,
        _make_summary(
            passed=0,
            failed=0,
            skipped=0,
            error=0,
            total=0,
            duration_ms=0,
        ),
    )
    card = build_run_card(run, "https://qa.example.com/report")

    all_text = str(card["card"]["elements"])
    assert "N/A" in all_text


def test_card_without_summary_shows_na() -> None:
    """When a run has no summary (e.g. FAILED without collection), show N/A."""
    run = _make_run(RunStatus.FAILED, summary=None)
    card = build_run_card(run, "https://qa.example.com/report")

    all_text = str(card["card"]["elements"])
    assert "N/A" in all_text


# ── send_feishu_card ──────────────────────────────────────────────────────


def _mock_response(status_code: int) -> AsyncMock:
    """Build an AsyncMock that behaves like an httpx.Response."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = "ok" if resp.is_success else "Bad Request"
    return resp


def _patch_post(resp: AsyncMock):
    """Patch httpx.AsyncClient.post to return *resp* when awaited."""

    async def _post_side_effect(*args, **kwargs):
        return resp

    return patch("httpx.AsyncClient.post", side_effect=_post_side_effect)


@pytest.mark.asyncio
async def test_send_feishu_card_success() -> None:
    """Successful POST with 200 returns True."""
    with _patch_post(_mock_response(200)):
        ok = await send_feishu_card(
            "https://open.feishu.cn/open-apis/bot/v2/hook/test",
            {"msg_type": "interactive", "card": {}},
        )
    assert ok is True


@pytest.mark.asyncio
async def test_send_feishu_card_http_error() -> None:
    """Non-2xx status returns False."""
    with _patch_post(_mock_response(400)):
        ok = await send_feishu_card("https://example.com/hook", {})
    assert ok is False


@pytest.mark.asyncio
async def test_send_feishu_card_network_error() -> None:
    """Network/timeout error returns False (never raises)."""

    async def _raise(*args, **kwargs):
        raise Exception("Connection refused")

    with patch("httpx.AsyncClient.post", side_effect=_raise):
        ok = await send_feishu_card("https://example.com/hook", {})
    assert ok is False
