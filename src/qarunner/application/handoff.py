"""Immutable execution-path handoff contracts for materialized Batch scope."""

import enum
from dataclasses import dataclass

from qarunner.domain.batch import BatchRejection
from qarunner.domain.cancellation import BatchCancellationIntent
from qarunner.domain.digest import Digest, canonical_digest

SCHEMA_VERSION = "qep.batch-materialized-scope-handoff.v1"
IDENTITY_ALGORITHM_VERSION = "canonical-sha256.v1"


class HandoffTriggerKind(enum.StrEnum):
    CANCEL_INTENT = "cancel_intent"
    REJECTION_CONFLICT = "rejection_conflict"


@dataclass(frozen=True, slots=True)
class BatchMaterializedScopeHandoff:
    schema_version: str
    identity_algorithm_version: str
    semantic_trigger_key: Digest
    handoff_id: str
    handoff_digest: Digest
    trigger_kind: HandoffTriggerKind
    command_or_observation_digest: Digest
    batch_id: str
    source_batch_version: int
    project_id: str
    suite_revision_id: str
    preplan_scope_digest: Digest | None
    manifest_digest: Digest | None
    shard_plan_version: int | None
    shard_plan_digest: Digest | None
    authoritative_run_set_digest: Digest
    authority_digest: Digest
    write_epoch: int
    destination: str
    event_id: str
    payload_digest: Digest


def build_cancel_handoff(
    *,
    intent: BatchCancellationIntent,
    source_batch_version: int,
    authoritative_run_set_digest: Digest,
    authority_digest: Digest,
    write_epoch: int,
) -> BatchMaterializedScopeHandoff:
    """Build the one deterministic execution-path handoff for a cancellation intent."""
    return _build_handoff(
        trigger_kind=HandoffTriggerKind.CANCEL_INTENT,
        trigger_digest=intent.digest,
        batch_id=intent.batch_id,
        source_batch_version=source_batch_version,
        project_id=intent.project_id,
        suite_revision_id=intent.suite_revision_id,
        preplan_scope_digest=intent.scope.preplan_scope_digest,
        manifest_digest=intent.scope.manifest_digest,
        shard_plan_version=intent.scope.shard_plan_version,
        shard_plan_digest=intent.scope.shard_plan_digest,
        authoritative_run_set_digest=authoritative_run_set_digest,
        authority_digest=authority_digest,
        write_epoch=write_epoch,
    )


def build_rejection_conflict_handoff(
    *,
    rejection: BatchRejection,
    project_id: str,
    suite_revision_id: str,
    preplan_scope_digest: Digest | None,
    manifest_digest: Digest | None,
    shard_plan_version: int | None,
    shard_plan_digest: Digest | None,
    authoritative_run_set_digest: Digest,
    authority_digest: Digest,
    write_epoch: int,
) -> BatchMaterializedScopeHandoff:
    """Build the execution-path observation for a rejection racing materialized Runs."""
    return _build_handoff(
        trigger_kind=HandoffTriggerKind.REJECTION_CONFLICT,
        trigger_digest=rejection.digest,
        batch_id=rejection.batch_id,
        source_batch_version=rejection.source_batch_version,
        project_id=project_id,
        suite_revision_id=suite_revision_id,
        preplan_scope_digest=preplan_scope_digest,
        manifest_digest=manifest_digest,
        shard_plan_version=shard_plan_version,
        shard_plan_digest=shard_plan_digest,
        authoritative_run_set_digest=authoritative_run_set_digest,
        authority_digest=authority_digest,
        write_epoch=write_epoch,
    )


def _build_handoff(
    *,
    trigger_kind: HandoffTriggerKind,
    trigger_digest: Digest,
    batch_id: str,
    source_batch_version: int,
    project_id: str,
    suite_revision_id: str,
    preplan_scope_digest: Digest | None,
    manifest_digest: Digest | None,
    shard_plan_version: int | None,
    shard_plan_digest: Digest | None,
    authoritative_run_set_digest: Digest,
    authority_digest: Digest,
    write_epoch: int,
) -> BatchMaterializedScopeHandoff:
    identity_payload = {
        "batch_id": batch_id,
        "command_or_observation_digest": trigger_digest.value,
        "schema_version": SCHEMA_VERSION,
        "trigger_kind": trigger_kind.value,
    }
    semantic_key = canonical_digest(
        schema_version="qep.batch-materialized-scope-handoff-identity.v1",
        payload=identity_payload,
    )
    identity_suffix = semantic_key.value.removeprefix("sha256:")
    binding_payload = {
        **identity_payload,
        "source_batch_version": source_batch_version,
        "project_id": project_id,
        "suite_revision_id": suite_revision_id,
        "preplan_scope_digest": _value(preplan_scope_digest),
        "manifest_digest": _value(manifest_digest),
        "shard_plan_version": shard_plan_version,
        "shard_plan_digest": _value(shard_plan_digest),
        "authoritative_run_set_digest": authoritative_run_set_digest.value,
        "authority_digest": authority_digest.value,
        "write_epoch": write_epoch,
        "destination": "execution_path",
    }
    handoff_digest = canonical_digest(schema_version=SCHEMA_VERSION, payload=binding_payload)
    payload_digest = canonical_digest(
        schema_version="qep.batch-materialized-scope-handoff-event-payload.v1",
        payload={"handoff_digest": handoff_digest.value},
    )
    return BatchMaterializedScopeHandoff(
        schema_version=SCHEMA_VERSION,
        identity_algorithm_version=IDENTITY_ALGORITHM_VERSION,
        semantic_trigger_key=semantic_key,
        handoff_id=f"handoff-{identity_suffix}",
        handoff_digest=handoff_digest,
        trigger_kind=trigger_kind,
        command_or_observation_digest=trigger_digest,
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        project_id=project_id,
        suite_revision_id=suite_revision_id,
        preplan_scope_digest=preplan_scope_digest,
        manifest_digest=manifest_digest,
        shard_plan_version=shard_plan_version,
        shard_plan_digest=shard_plan_digest,
        authoritative_run_set_digest=authoritative_run_set_digest,
        authority_digest=authority_digest,
        write_epoch=write_epoch,
        destination="execution_path",
        event_id=f"handoff-event-{identity_suffix}",
        payload_digest=payload_digest,
    )


def _value(digest: Digest | None) -> str | None:
    return None if digest is None else digest.value
