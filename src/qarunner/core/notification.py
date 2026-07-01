"""Feishu (Lark) bot notification — card builder and sender for run-completion."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from qarunner.models import Run, RunStatus

logger = logging.getLogger(__name__)


def _status_color(status: RunStatus) -> str:
    """Map a terminal RunStatus to a Feishu card header color."""
    _COLORS: dict[RunStatus, str] = {
        RunStatus.COMPLETED: "green",
        RunStatus.FAILED: "red",
        RunStatus.TIMEOUT: "orange",
        RunStatus.CANCELLED: "yellow",
    }
    return _COLORS.get(status, "red")


def _pass_rate_text(run: Run) -> str:
    if run.summary is None or run.summary.total == 0:
        return "N/A"
    pct = run.summary.pass_rate * 100
    return f"{pct:.1f}%"


def _stat_line(label: str, count: int, total: int) -> str:
    if total == 0:
        return f"{label}: {count} (N/A)"
    pct = (count / total) * 100
    return f"{label}: {count} ({pct:.1f}%)"


def build_run_card(run: Run, allure_url: str) -> dict[str, Any]:
    """Build a Feishu interactive card JSON for a completed run.

    The card shows the run ID, pass rate, per-status counts, and a button
    linking to the Allure report.  Feishu card reference:
    https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components
    """
    summary = run.summary
    total = summary.total if summary else 0

    header_title = (
        f"qarunner 测试报告 — {run.id[:8]}… "
        f"通过率 {_pass_rate_text(run)}"
    )

    # Build the markdown statistics block.
    if summary is not None:
        stats = "\n".join([
            _stat_line("✅ 通过", summary.passed, total),
            _stat_line("❌ 失败", summary.failed, total),
            _stat_line("⏭️  跳过", summary.skipped, total),
            _stat_line("⚠️  错误", summary.error, total),
            f"⏱  耗时: {summary.duration_ms / 1000:.1f}s",
        ])
    else:
        stats = "结果摘要: N/A"

    status_text = {  # type: ignore[var-annotated]
        RunStatus.COMPLETED: "已完成",
        RunStatus.FAILED: "执行失败",
        RunStatus.TIMEOUT: "执行超时",
        RunStatus.CANCELLED: "已取消",
    }.get(run.status, str(run.status.value))

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": _status_color(run.status),
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**状态**: {status_text}\n"
                            f"**套件**: {run.tests_path}\n"
                            f"**Runner**: {run.runner}\n\n"
                            f"{stats}"
                        ),
                    },
                },
                {
                    "tag": "hr",
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📊 Allure 报告"},
                            "type": "default",
                            "url": allure_url,
                        },
                    ],
                },
            ],
        },
    }
    return card


async def send_feishu_card(webhook_url: str, card: dict[str, Any]) -> bool:
    """POST a card to a Feishu bot webhook.  Returns True on 2xx, False on failure.

    Network errors and timeouts are caught and logged — the caller should treat
    this as fire-and-forget; a False return means the notification was not
    delivered.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=card,
                headers={"Content-Type": "application/json"},
            )
        if resp.is_success:
            return True
        logger.warning(
            "Feishu webhook returned %d: %s", resp.status_code, resp.text[:500]
        )
        return False
    except Exception:
        logger.exception("Failed to send Feishu card (webhook URL redacted)")
        return False
