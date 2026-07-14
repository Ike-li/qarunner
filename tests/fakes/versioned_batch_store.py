"""Deterministic single-Batch CAS Fake for domain race tests."""

from __future__ import annotations

from qarunner.domain import Batch, VersionConflict
from qarunner.domain.errors import ensure_expected_version


class InMemoryVersionedBatchStore:
    """Atomically replace one immutable Batch in deterministic, single-threaded tests.

    This Fake models only compare-and-swap publication. It does not model a database
    transaction, locking, durability, outbox delivery, or crash recovery.
    """

    def __init__(self, current: Batch) -> None:
        self.__current = current

    @property
    def current(self) -> Batch:
        """Return the currently committed immutable Batch snapshot."""
        return self.__current

    def commit(
        self,
        *,
        source: Batch,
        candidate: Batch,
        expected_version: int,
    ) -> Batch:
        """Publish one candidate only when its exact source is still current."""
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.__current.id,
            current_version=self.__current.version,
            expected_version=expected_version,
        )
        if source.id != self.__current.id or source != self.__current:
            raise VersionConflict(
                entity_type="batch",
                entity_id=self.__current.id,
                current_version=self.__current.version,
                expected_version=source.version,
            )
        if source.version != expected_version:
            raise VersionConflict(
                entity_type="batch",
                entity_id=self.__current.id,
                current_version=source.version,
                expected_version=expected_version,
            )
        if candidate.id != source.id:
            raise VersionConflict(
                entity_type="batch",
                entity_id=self.__current.id,
                current_version=self.__current.version,
                expected_version=expected_version,
            )
        required_candidate_version = source.version + 1
        if candidate.version != required_candidate_version:
            raise VersionConflict(
                entity_type="batch",
                entity_id=self.__current.id,
                current_version=candidate.version,
                expected_version=required_candidate_version,
            )
        self.__current = candidate
        return candidate
