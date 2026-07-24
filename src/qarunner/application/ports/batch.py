"""Batch create gateway with scoped idempotency (T-M2-BATCH-001).

M2 single-ECS scope. Insert-only create under UNIQUE (idempotency_scope,
idempotency_key): exact digest replay returns the original Batch; digest
mismatch raises IdempotencyConflict. Bindings always pin project_id +
suite_revision_id from the domain-opened aggregate.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import ReplayResult
from qarunner.domain import Batch


@runtime_checkable
class BatchGateway(Protocol):
    """Persist Batch create with scoped idempotency.

    Callers build a create-identity Batch via `Batch.open_for_suite` (domain)
    and hand it to `create`. Concurrent identical (scope, key, digest) requests
    collapse to one row; a reused key with a different digest is a stable
    conflict. Adapters re-check that the bound Suite still accepts new batches.
    """

    async def create(self, *, batch: Batch) -> ReplayResult[Batch]: ...

    async def get_batch(self, *, batch_id: str) -> Batch: ...

    async def get_by_idempotency(
        self, *, idempotency_scope: str, idempotency_key: str
    ) -> Batch | None: ...
