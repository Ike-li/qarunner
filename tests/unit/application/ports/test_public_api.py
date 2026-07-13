"""T-M0-PORT-001 package-level application port facade."""

from qarunner.application.ports import (
    ApplicationUnitOfWork,
    AttemptEventLog,
    AuditLog,
    AuditRecord,
    EvidenceManifestIndex,
    EvidenceVerificationRequest,
    EvidenceVerifier,
    ExecutionStopControl,
    ExecutionStopReceipt,
    ExecutionStopRequest,
    FactCommitResult,
    FactKey,
    OpaqueIdGenerator,
    PortContractError,
    ReplayResult,
    SecretBroker,
    SecretDeliveryLease,
    SecretRequest,
    UtcClock,
    VerifiedEvidenceInputs,
    VersionedFactCommand,
    VersionedFactStore,
)


def test_application_ports_are_available_from_the_public_facade() -> None:
    exported_ports = (
        ApplicationUnitOfWork,
        AttemptEventLog,
        AuditLog,
        AuditRecord,
        EvidenceManifestIndex,
        EvidenceVerificationRequest,
        EvidenceVerifier,
        ExecutionStopControl,
        ExecutionStopReceipt,
        ExecutionStopRequest,
        FactCommitResult,
        FactKey,
        OpaqueIdGenerator,
        PortContractError,
        ReplayResult,
        SecretBroker,
        SecretDeliveryLease,
        SecretRequest,
        UtcClock,
        VerifiedEvidenceInputs,
        VersionedFactCommand,
        VersionedFactStore,
    )

    assert all(
        exported_port.__module__.startswith("qarunner.application.ports")
        for exported_port in exported_ports
    )
