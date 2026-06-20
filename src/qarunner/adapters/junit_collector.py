"""Real result collector that delegates to the junit XML parser."""

from __future__ import annotations

from qarunner.core.junit import parse_junit_xml
from qarunner.models import CollectResult


class JunitCollector:
    """Collect test results from a junit.xml file in the run directory."""

    def collect(self, run_dir: str) -> CollectResult | None:
        return parse_junit_xml(f"{run_dir}/junit.xml")
