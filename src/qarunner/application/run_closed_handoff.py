"""Immutable Run-closed facts handed from 001G to Batch finalization."""

from dataclasses import dataclass

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run_finalization import (
    RunFinalizationBasis,
    RunItemResolutionSet,
    RunOutcome,
    TerminalInputKind,
)

SCHEMA_VERSION = "qep.run-closed-handoff.v1"
IDENTITY_ALGORITHM_VERSION = "canonical-sha256.v1"


@dataclass(frozen=True, slots=True)
class RunClosedHandoff:
    schema_version: str
    identity_algorithm_version: str
    semantic_trigger_key: Digest
    handoff_id: str
    event_id: str
    batch_id: str
    run_id: str
    source_run_version: int
    run_basis_digest: Digest
    run_outcome: RunOutcome
    terminal_input_kind: TerminalInputKind
    manifest_digest: Digest
    shard_plan_digest: Digest
    run_item_set_digest: Digest
    original_resolution_set_digest: Digest
    effective_resolution_set_digest: Digest
    item_resolution_set_digest: Digest
    item_count: int
    cancellation_intent_digest: Digest | None
    unknown_observation_digest: Digest | None
    adjudication_chain_digest: Digest | None
    retry_chain_digest: Digest | None
    authority_digest: Digest
    write_epoch: int
    destination: str
    payload_digest: Digest

    def __post_init__(self) -> None:
        if (self.schema_version, self.identity_algorithm_version, self.destination) != (
            SCHEMA_VERSION,
            IDENTITY_ALGORITHM_VERSION,
            "batch_finalization",
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="envelope", reason="invalid"
            )
        if any(
            not isinstance(getattr(self, f), str) or not getattr(self, f).strip()
            for f in ("handoff_id", "event_id", "batch_id", "run_id")
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="identity", reason="invalid"
            )
        if any(
            isinstance(getattr(self, f), bool) or not isinstance(getattr(self, f), int)
            for f in ("source_run_version", "item_count", "write_epoch")
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="integer", reason="invalid"
            )
        if self.source_run_version < 0 or self.item_count < 1 or self.write_epoch < 1:
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="integer", reason="invalid"
            )
        digests = (
            "semantic_trigger_key",
            "run_basis_digest",
            "manifest_digest",
            "shard_plan_digest",
            "run_item_set_digest",
            "original_resolution_set_digest",
            "effective_resolution_set_digest",
            "item_resolution_set_digest",
            "authority_digest",
            "payload_digest",
        )
        if any(not isinstance(getattr(self, f), Digest) for f in digests):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="digest", reason="invalid"
            )
        optional = (
            "cancellation_intent_digest",
            "unknown_observation_digest",
            "adjudication_chain_digest",
            "retry_chain_digest",
        )
        if any(
            getattr(self, f) is not None and not isinstance(getattr(self, f), Digest)
            for f in optional
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="digest", reason="invalid"
            )
        if not isinstance(self.run_outcome, RunOutcome) or not isinstance(
            self.terminal_input_kind, TerminalInputKind
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="enum", reason="invalid"
            )
        self.validate_self_consistency()

    def validate_self_consistency(self) -> None:
        identity = _identity(self.run_id, self.source_run_version, self.run_basis_digest)
        suffix = identity.value.removeprefix("sha256:")
        if (
            self.semantic_trigger_key != identity
            or self.handoff_id != f"run-closed-handoff-{suffix}"
            or self.event_id != f"run-closed-event-{suffix}"
            or self.payload_digest != _rebuild_payload(self)
        ):
            raise DomainValidationError(
                entity_type="run_closed_handoff", field="self_consistency", reason="invalid"
            )


def build_run_closed_handoff(
    *,
    basis: RunFinalizationBasis,
    resolution_set: RunItemResolutionSet,
    authority_digest: Digest,
    write_epoch: int,
) -> RunClosedHandoff:
    if (
        basis.run_id != resolution_set.run_id
        or basis.batch_id != resolution_set.batch_id
        or basis.source_run_version != resolution_set.source_run_version
        or basis.item_resolution_set_digest != resolution_set.resolution_set_digest
        or basis.original_resolution_set_digest != resolution_set.original_resolution_set_digest
        or basis.effective_resolution_set_digest != resolution_set.effective_resolution_set_digest
        or basis.run_item_set_digest != resolution_set.run_item_set_digest
        or basis.item_count != resolution_set.item_count
        or basis.outcome is not resolution_set.audit_outcome
    ):
        raise ValueError("run_closed_handoff/binding_mismatch")
    identity = canonical_digest(
        schema_version="qep.run-closed-handoff-identity.v1",
        payload={
            "schema_version": SCHEMA_VERSION,
            "run_id": basis.run_id,
            "source_run_version": basis.source_run_version,
            "run_basis_digest": basis.basis_digest.value,
        },
    )
    suffix = identity.value.removeprefix("sha256:")
    values = {
        "batch_id": basis.batch_id,
        "run_id": basis.run_id,
        "source_run_version": basis.source_run_version,
        "run_basis_digest": basis.basis_digest.value,
        "run_outcome": basis.outcome.value,
        "terminal_input_kind": basis.terminal_input_kind.value,
        "manifest_digest": basis.manifest_digest.value,
        "shard_plan_digest": basis.shard_plan_digest.value,
        "run_item_set_digest": basis.run_item_set_digest.value,
        "original_resolution_set_digest": basis.original_resolution_set_digest.value,
        "effective_resolution_set_digest": basis.effective_resolution_set_digest.value,
        "item_resolution_set_digest": basis.item_resolution_set_digest.value,
        "item_count": basis.item_count,
        "cancellation_intent_digest": _value(basis.cancellation_intent_digest),
        "unknown_observation_digest": _value(basis.unknown_observation_digest),
        "adjudication_chain_digest": _value(basis.adjudication_chain_digest),
        "retry_chain_digest": _value(basis.retry_chain_digest),
        "authority_digest": authority_digest.value,
        "write_epoch": write_epoch,
        "destination": "batch_finalization",
    }
    binding = canonical_digest(schema_version=SCHEMA_VERSION, payload=values)
    payload = canonical_digest(
        schema_version="qep.run-closed-handoff-event-payload.v1",
        payload={"handoff_digest": binding.value},
    )
    return RunClosedHandoff(
        SCHEMA_VERSION,
        IDENTITY_ALGORITHM_VERSION,
        identity,
        f"run-closed-handoff-{suffix}",
        f"run-closed-event-{suffix}",
        basis.batch_id,
        basis.run_id,
        basis.source_run_version,
        basis.basis_digest,
        basis.outcome,
        basis.terminal_input_kind,
        basis.manifest_digest,
        basis.shard_plan_digest,
        basis.run_item_set_digest,
        basis.original_resolution_set_digest,
        basis.effective_resolution_set_digest,
        basis.item_resolution_set_digest,
        basis.item_count,
        basis.cancellation_intent_digest,
        basis.unknown_observation_digest,
        basis.adjudication_chain_digest,
        basis.retry_chain_digest,
        authority_digest,
        write_epoch,
        "batch_finalization",
        payload,
    )


def _value(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _identity(run_id: str, source_run_version: int, basis_digest: Digest) -> Digest:
    return canonical_digest(
        schema_version="qep.run-closed-handoff-identity.v1",
        payload={
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "source_run_version": source_run_version,
            "run_basis_digest": basis_digest.value,
        },
    )


def _rebuild_payload(value: RunClosedHandoff) -> Digest:
    binding = {
        "batch_id": value.batch_id,
        "run_id": value.run_id,
        "source_run_version": value.source_run_version,
        "run_basis_digest": value.run_basis_digest.value,
        "run_outcome": value.run_outcome.value,
        "terminal_input_kind": value.terminal_input_kind.value,
        "manifest_digest": value.manifest_digest.value,
        "shard_plan_digest": value.shard_plan_digest.value,
        "run_item_set_digest": value.run_item_set_digest.value,
        "original_resolution_set_digest": value.original_resolution_set_digest.value,
        "effective_resolution_set_digest": value.effective_resolution_set_digest.value,
        "item_resolution_set_digest": value.item_resolution_set_digest.value,
        "item_count": value.item_count,
        "cancellation_intent_digest": _value(value.cancellation_intent_digest),
        "unknown_observation_digest": _value(value.unknown_observation_digest),
        "adjudication_chain_digest": _value(value.adjudication_chain_digest),
        "retry_chain_digest": _value(value.retry_chain_digest),
        "authority_digest": value.authority_digest.value,
        "write_epoch": value.write_epoch,
        "destination": value.destination,
    }
    digest = canonical_digest(schema_version=SCHEMA_VERSION, payload=binding)
    return canonical_digest(
        schema_version="qep.run-closed-handoff-event-payload.v1",
        payload={"handoff_digest": digest.value},
    )
