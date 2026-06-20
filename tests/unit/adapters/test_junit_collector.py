"""Tests for JunitCollector adapter."""

from __future__ import annotations

from pathlib import Path

from qarunner.adapters.junit_collector import JunitCollector


def test_collect_with_valid_xml(tmp_path: Path) -> None:
    xml_content = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="suite1" tests="2" failures="1">
    <testcase name="test_pass" classname="suite1" time="0.1"/>
    <testcase name="test_fail" classname="suite1" time="0.2">
      <failure>AssertionError</failure>
    </testcase>
  </testsuite>
</testsuites>
"""
    run_dir = str(tmp_path)
    (tmp_path / "junit.xml").write_text(xml_content)

    collector = JunitCollector()
    result = collector.collect(run_dir)
    assert result is not None
    assert result.summary.total == 2
    assert result.summary.passed == 1
    assert result.summary.failed == 1
    assert len(result.cases) == 2


def test_collect_missing_file_returns_none(tmp_path: Path) -> None:
    collector = JunitCollector()
    result = collector.collect(str(tmp_path))
    assert result is None


def test_collect_invalid_xml_returns_none(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text("not xml")
    collector = JunitCollector()
    result = collector.collect(str(tmp_path))
    assert result is None
