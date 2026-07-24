"""In-memory `SuiteGateway` test double for unit-testing the command flow.

Real optimistic-CAS and immutability of revision rows are proven against
PostgreSQL in the SUITE-PERSIST integration matrix.
"""

from __future__ import annotations

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.suite import SuiteGateway, SuiteMutationSnapshot
from qarunner.domain import Suite
from qarunner.domain.errors import VersionConflict


class InMemorySuiteGateway(SuiteGateway):
    def __init__(self) -> None:
        self._suites: dict[str, Suite] = {}

    def seed(self, suite: Suite) -> None:
        self._suites[suite.id] = suite

    async def get_suite_for_update(self, *, suite_id: str) -> SuiteMutationSnapshot:
        suite = self._suites.get(suite_id)
        if suite is None:
            raise PortContractError(resource="suite_gateway", field="suite_id", reason="not_found")
        return SuiteMutationSnapshot(suite=suite)

    async def register(self, *, suite: Suite) -> ReplayResult[Suite]:
        stored = self._suites.get(suite.id)
        if stored is not None:
            if stored == suite:
                return ReplayResult(value=stored, replayed=True)
            raise PortContractError(
                resource="suite_gateway", field="suite_id", reason="already_exists"
            )
        # Unique (project_id, name) analogue for the Fake.
        for existing in self._suites.values():
            if existing.project_id == suite.project_id and existing.name == suite.name:
                raise PortContractError(
                    resource="suite_gateway", field="name", reason="duplicate_name"
                )
        self._suites[suite.id] = suite
        return ReplayResult(value=suite, replayed=False)

    async def publish_revision(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        return self._persist(suite, expected)

    async def publish_retire(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        return self._persist(suite, expected)

    def _persist(self, suite: Suite, expected: SuiteMutationSnapshot) -> ReplayResult[Suite]:
        current = self._suites.get(expected.suite_id)
        if current is None or current.version != expected.version:
            raise VersionConflict(
                entity_type="suite",
                entity_id=expected.suite_id,
                current_version=0 if current is None else current.version,
                expected_version=expected.version,
            )
        if suite.id != expected.suite_id:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="suite_id_mismatch"
            )
        self._suites[suite.id] = suite
        return ReplayResult(value=suite, replayed=False)
