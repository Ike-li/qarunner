"""In-memory `BatchGateway` test double for unit-testing the create flow.

Real concurrent UNIQUE(idempotency_scope, key) election and Suite retire races
are proven against PostgreSQL in the BATCH-IDEM integration matrix.
"""

from __future__ import annotations

from qarunner.application.ports.batch import BatchGateway
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Batch, IdempotencyConflict
from qarunner.domain.suite import Suite


class InMemoryBatchGateway(BatchGateway):
    def __init__(self) -> None:
        self._batches: dict[str, Batch] = {}
        self._by_idem: dict[tuple[str, str], str] = {}
        self._suites: dict[str, Suite] = {}
        # revision_id -> suite_id for retire checks
        self._revision_suite: dict[str, str] = {}

    def seed_suite(self, suite: Suite) -> None:
        self._suites[suite.id] = suite
        for revision in suite.revisions:
            self._revision_suite[revision.id] = suite.id

    async def create(self, *, batch: Batch) -> ReplayResult[Batch]:
        if not batch.has_create_identity:
            raise PortContractError(
                resource="batch_gateway", field="create_identity", reason="missing"
            )
        assert batch.idempotency_scope is not None
        assert batch.idempotency_key is not None
        assert batch.suite_revision_id is not None
        assert batch.request_digest is not None

        suite_id = self._revision_suite.get(batch.suite_revision_id)
        if suite_id is None:
            raise PortContractError(
                resource="batch_gateway",
                field="suite_revision_id",
                reason="not_found",
            )
        suite = self._suites[suite_id]
        suite.require_accepts_new_batch()

        key = (batch.idempotency_scope, batch.idempotency_key)
        existing_id = self._by_idem.get(key)
        if existing_id is not None:
            stored = self._batches[existing_id]
            assert stored.request_digest is not None
            if stored.request_digest != batch.request_digest:
                raise IdempotencyConflict(
                    scope=batch.idempotency_scope,
                    key=batch.idempotency_key,
                    stored_digest=stored.request_digest,
                    received_digest=batch.request_digest,
                )
            return ReplayResult(value=stored, replayed=True)

        if batch.id in self._batches:
            raise PortContractError(
                resource="batch_gateway", field="batch_id", reason="already_exists"
            )
        self._batches[batch.id] = batch
        self._by_idem[key] = batch.id
        return ReplayResult(value=batch, replayed=False)

    async def get_batch(self, *, batch_id: str) -> Batch:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise PortContractError(resource="batch_gateway", field="batch_id", reason="not_found")
        return batch

    async def get_by_idempotency(
        self, *, idempotency_scope: str, idempotency_key: str
    ) -> Batch | None:
        batch_id = self._by_idem.get((idempotency_scope, idempotency_key))
        if batch_id is None:
            return None
        return self._batches[batch_id]
