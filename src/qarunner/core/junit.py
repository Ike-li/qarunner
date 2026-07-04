"""Pure-function junit XML parser — extracts test results from junit.xml."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from qarunner.models import CollectResult, TestCaseResult, TestSummary

# A junit.xml is produced by untrusted test code, so parse it defensively:
# defusedxml blocks XXE / entity-expansion (billion-laughs) attacks, and we cap
# the file size so a giant report can't exhaust memory before parsing (SEC-7).
_MAX_JUNIT_BYTES = 10 * 1024 * 1024


def parse_junit_xml(path: str) -> CollectResult | None:
    """Parse a junit.xml file and return a ``CollectResult``.

    Returns ``None`` if the file does not exist or cannot be parsed.
    """
    p = Path(path)
    if not p.is_file():
        return None
    if p.stat().st_size > _MAX_JUNIT_BYTES:
        return None

    try:
        tree = DefusedET.parse(p)
    except (ET.ParseError, DefusedXmlException):
        return None

    root = tree.getroot()

    # Support both <testsuites><testsuite ...> and bare <testsuite ...>
    suite_elements: list[ET.Element] = []
    if root.tag == "testsuites":
        suite_elements = list(root.findall("testsuite"))
    elif root.tag == "testsuite":
        suite_elements = [root]
    else:
        return None

    cases: list[TestCaseResult] = []
    for suite_el in suite_elements:
        suite_name = suite_el.get("name", "")
        for tc_el in suite_el.findall("testcase"):
            tc_name = tc_el.get("name", "")
            duration_ms = _duration_ms(tc_el.get("time"))

            failure_el = tc_el.find("failure")
            error_el = tc_el.find("error")
            skipped_el = tc_el.find("skipped")

            if failure_el is not None:
                status = "failed"
                message = _text_or_none(failure_el)
            elif error_el is not None:
                status = "error"
                message = _text_or_none(error_el)
            elif skipped_el is not None:
                status = "skipped"
                message = _text_or_none(skipped_el)
            else:
                status = "passed"
                message = _text_or_none(None)

            cases.append(
                TestCaseResult(
                    suite=suite_name,
                    name=tc_name,
                    status=status,
                    duration_ms=duration_ms,
                    message=message,
                )
            )

    if not cases:
        return None

    total = len(cases)
    passed = sum(1 for c in cases if c.status == "passed")
    failed = sum(1 for c in cases if c.status == "failed")
    skipped = sum(1 for c in cases if c.status == "skipped")
    error = sum(1 for c in cases if c.status == "error")
    total_ms = sum(c.duration_ms for c in cases)

    summary = TestSummary(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        error=error,
        duration_ms=total_ms,
    )
    return CollectResult(summary=summary, cases=cases)


def _duration_ms(raw: str | None) -> int:
    """Convert a junit ``time`` (seconds) to milliseconds, tolerating a missing
    or non-numeric value. junit.xml comes from untrusted test code, so a
    malformed ``time`` must not raise (parse_junit_xml only returns None on
    unparseable XML); the case is kept with zero duration instead."""
    try:
        return int(float(raw or "0") * 1000)
    except ValueError:
        return 0


def _text_or_none(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return el.text or None
