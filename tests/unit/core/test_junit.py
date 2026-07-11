"""Tests for qarunner.core.junit — parse_junit_xml."""

import pytest

from qarunner.core.junit import _text_or_none, parse_junit_xml


class TestParseJUnitXml:
    """parse_junit_xml extracts test results from junit XML files."""

    def test_returns_none_for_missing_file(self, tmp_path):
        result = parse_junit_xml(str(tmp_path / "nope.xml"))
        assert result is None

    def test_returns_none_for_invalid_xml(self, tmp_path):
        bad = tmp_path / "bad.xml"
        bad.write_text("not xml at all")
        result = parse_junit_xml(str(bad))
        assert result is None

    def test_returns_none_for_entity_expansion(self, tmp_path):
        """SEC-7: an entity-expansion (billion-laughs) junit.xml is rejected safely."""
        f = tmp_path / "evil.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            "<!DOCTYPE testsuite [\n"
            '  <!ENTITY lol "lol">\n'
            '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">\n'
            "]>\n"
            '<testsuite name="s">\n'
            '  <testcase name="&lol2;" classname="s" time="0.1"/>\n'
            "</testsuite>\n"
        )
        assert parse_junit_xml(str(f)) is None

    def test_returns_none_for_oversized_file(self, tmp_path, monkeypatch):
        """SEC-7: a junit.xml larger than the size cap is rejected before parsing."""
        from qarunner.core import junit

        monkeypatch.setattr(junit, "_MAX_JUNIT_BYTES", 10)
        f = tmp_path / "big.xml"
        f.write_text('<?xml version="1.0"?><testsuite name="s"></testsuite>')
        assert parse_junit_xml(str(f)) is None

    def test_returns_none_for_unknown_root(self, tmp_path):
        f = tmp_path / "weird.xml"
        f.write_text('<?xml version="1.0"?><unknown/>')
        result = parse_junit_xml(str(f))
        assert result is None

    def test_returns_none_for_empty_testsuites(self, tmp_path):
        f = tmp_path / "empty.xml"
        f.write_text('<?xml version="1.0"?><testsuites/>')
        result = parse_junit_xml(str(f))
        assert result is None

    def test_parses_testsuites_root(self, tmp_path):
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            "<testsuites>\n"
            '  <testsuite name="suite1" tests="3">\n'
            '    <testcase name="test_pass" classname="suite1" time="0.1"/>\n'
            '    <testcase name="test_fail" classname="suite1" time="0.2">\n'
            '      <failure message="bad">stacktrace</failure>\n'
            "    </testcase>\n"
            '    <testcase name="test_skip" classname="suite1" time="0.0">\n'
            '      <skipped message="not ready"/>\n'
            "    </testcase>\n"
            "  </testsuite>\n"
            "</testsuites>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.summary.total == 3
        assert result.summary.passed == 1
        assert result.summary.failed == 1
        assert result.summary.skipped == 1
        assert result.summary.error == 0
        assert result.summary.duration_ms == 300  # 100+200+0
        assert len(result.cases) == 3

    def test_parses_bare_testsuite_root(self, tmp_path):
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s" tests="1">\n'
            '  <testcase name="ok" classname="s" time="0.5"/>\n'
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.summary.total == 1
        assert result.summary.passed == 1
        assert result.cases[0].duration_ms == 500

    def test_error_status(self, tmp_path):
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s">\n'
            '  <testcase name="boom" classname="s" time="0.01">\n'
            '    <error type="RuntimeError">exploded</error>\n'
            "  </testcase>\n"
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.cases[0].status == "error"
        assert result.cases[0].message == "exploded"
        assert result.summary.error == 1

    def test_failure_with_no_text(self, tmp_path):
        """Failure element present but with no text content."""
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s">\n'
            '  <testcase name="bare_fail" classname="s" time="0.1">\n'
            '    <failure type="AssertionError"/>\n'
            "  </testcase>\n"
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.cases[0].status == "failed"
        assert result.cases[0].message is None

    def test_pass_rate(self, tmp_path):
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s">\n'
            '  <testcase name="a" classname="s" time="0.1"/>\n'
            '  <testcase name="b" classname="s" time="0.1">\n'
            "    <failure>f</failure>\n"
            "  </testcase>\n"
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.summary.pass_rate == pytest.approx(0.5)

    def test_non_numeric_time_treated_as_zero(self, tmp_path):
        """A non-numeric ``time`` (untrusted producer) must not raise or discard
        the result; the case is kept with zero duration. parse_junit_xml is
        documented to never raise on parseable XML — only return None."""
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s" tests="1">\n'
            '  <testcase name="ok" classname="s" time="N/A"/>\n'
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.summary.total == 1
        assert result.summary.passed == 1
        assert result.cases[0].duration_ms == 0

    def test_infinite_time_treated_as_zero(self, tmp_path):
        """BUG-26: Python's float() happily parses "inf", but
        int(float("inf") * 1000) raises OverflowError rather than ValueError
        — an untrusted-producer edge case the old except clause didn't
        catch, breaking the same "never raise" contract as the non-numeric
        case above."""
        f = tmp_path / "junit.xml"
        f.write_text(
            '<?xml version="1.0"?>\n'
            '<testsuite name="s" tests="1">\n'
            '  <testcase name="ok" classname="s" time="inf"/>\n'
            "</testsuite>\n"
        )
        result = parse_junit_xml(str(f))
        assert result is not None
        assert result.cases[0].duration_ms == 0


class TestTextOrNone:
    """_text_or_none helper edge cases."""

    def test_returns_none_for_none_element(self):
        assert _text_or_none(None) is None

    def test_returns_text_content(self, tmp_path):
        import xml.etree.ElementTree as ET

        el = ET.fromstring("<failure>oops</failure>")
        assert _text_or_none(el) == "oops"

    def test_returns_none_for_empty_text(self, tmp_path):
        import xml.etree.ElementTree as ET

        el = ET.fromstring("<failure/>")
        assert _text_or_none(el) is None
