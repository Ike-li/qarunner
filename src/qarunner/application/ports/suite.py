"""Suite aggregate gateway (register / publish_revision / retire).

M2 single-ECS scope (`T-M2-SUITE-001` persistence half). Mirrors the
AssignmentGateway shape: load under FOR UPDATE, apply domain command, publish
with optimistic CAS on suite.version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Suite


@dataclass(frozen=True, slots=True)
class SuiteMutationSnapshot:
    """The Suite aggregate loaded under `FOR UPDATE`, carrying its CAS version."""

    suite: Suite

    def __post_init__(self) -> None:
        if not isinstance(self.suite, Suite):
            raise PortContractError(
                resource="suite_mutation_snapshot", field="suite", reason="not_suite"
            )

    @property
    def suite_id(self) -> str:
        return self.suite.id

    @property
    def version(self) -> int:
        return self.suite.version


@runtime_checkable
class SuiteGateway(Protocol):
    """Load-and-persist Suite lifecycle transitions.

    `register` creates the suite + first revision. `get_suite_for_update` +
    `publish_revision` / `publish_retire` apply domain results under CAS.
    """

    async def get_suite_for_update(self, *, suite_id: str) -> SuiteMutationSnapshot: ...

    async def register(self, *, suite: Suite) -> ReplayResult[Suite]: ...

    async def publish_revision(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]: ...

    async def publish_retire(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]: ...
