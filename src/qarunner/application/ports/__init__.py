"""Application-facing ports for the greenfield control plane."""

from qarunner.application.ports.audit import AuditLog, AuditRecord
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionStamp,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
    BatchCancellationAuthority,
    BatchCancellationSideEffect,
    BatchClosureAuthority,
    BatchClosureSideEffect,
    BatchPreexecutionGateway,
    BatchRejectionAuthority,
    BatchRejectionSideEffect,
)
from qarunner.application.ports.clock import UtcClock
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.evidence import (
    EvidenceManifestIndex,
    EvidenceVerificationRequest,
    EvidenceVerifier,
    VerifiedEvidenceInputs,
)
from qarunner.application.ports.execution import (
    ExecutionStopControl,
    ExecutionStopReceipt,
    ExecutionStopRequest,
)
from qarunner.application.ports.facts import (
    AttemptEventLog,
    FactCommitResult,
    FactKey,
    VersionedFactCommand,
    VersionedFactStore,
)
from qarunner.application.ports.handoff import (
    HandoffDeliveryMetadata,
    HandoffEventBlocked,
    HandoffEventConflict,
    HandoffPoisonAlert,
    MaterializedScopeHandoffConsumer,
)
from qarunner.application.ports.ids import OpaqueIdGenerator
from qarunner.application.ports.preexecution_proof import (
    ExecutionChildInventory,
    PreexecutionProofGateway,
    PreexecutionTaskGeneration,
    PreexecutionTaskKey,
    SealedPlannedScopeInventory,
    SealedTaskInventory,
    TaskLedgerPosition,
    TrustedTaskStop,
    ZeroChildSnapshotInputs,
    canonical_task_set_digest,
)
from qarunner.application.ports.secrets import (
    SecretBroker,
    SecretDeliveryLease,
    SecretRequest,
)
from qarunner.application.ports.transactions import ApplicationUnitOfWork

__all__ = [
    "ApplicationUnitOfWork",
    "AttemptEventLog",
    "AuthorityPermissionDenied",
    "AuthorityProjectionUnavailable",
    "AuthorityProjectionStamp",
    "AuthorityStateConflict",
    "AuditLog",
    "AuditRecord",
    "BatchCancellationAuthority",
    "BatchCancellationSideEffect",
    "BatchClosureSideEffect",
    "BatchClosureAuthority",
    "BatchPreexecutionGateway",
    "BatchRejectionAuthority",
    "BatchRejectionSideEffect",
    "EvidenceManifestIndex",
    "EvidenceVerificationRequest",
    "EvidenceVerifier",
    "ExecutionStopControl",
    "ExecutionStopReceipt",
    "ExecutionStopRequest",
    "ExecutionChildInventory",
    "FactCommitResult",
    "FactKey",
    "HandoffEventBlocked",
    "HandoffDeliveryMetadata",
    "HandoffEventConflict",
    "HandoffPoisonAlert",
    "MaterializedScopeHandoffConsumer",
    "OpaqueIdGenerator",
    "PortContractError",
    "PreexecutionProofGateway",
    "PreexecutionTaskGeneration",
    "PreexecutionTaskKey",
    "ReplayResult",
    "SecretBroker",
    "SecretDeliveryLease",
    "SecretRequest",
    "SealedTaskInventory",
    "SealedPlannedScopeInventory",
    "TaskLedgerPosition",
    "UtcClock",
    "TrustedTaskStop",
    "VerifiedEvidenceInputs",
    "VersionedFactCommand",
    "VersionedFactStore",
    "ZeroChildSnapshotInputs",
    "canonical_task_set_digest",
]
