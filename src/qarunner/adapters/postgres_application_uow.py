"""PostgreSQL aggregate transaction for generic Fact, Evidence, and Audit ports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self, cast

import asyncpg

from qarunner.application.ports.audit import AuditLog, AuditRecord
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.evidence import EvidenceManifestIndex
from qarunner.application.ports.facts import (
    FactCommitResult,
    FactKey,
    FactT,
    VersionedFact,
    VersionedFactCommand,
    VersionedFactStore,
)
from qarunner.domain.authority import WorkerRef
from qarunner.domain.cancellation import TrustedCancellationStop
from qarunner.domain.digest import Digest, JsonValue, canonical_digest
from qarunner.domain.errors import EvidenceConflict, IdempotencyConflict, VersionConflict
from qarunner.domain.evidence import (
    ArtifactClass,
    ArtifactPath,
    EvidenceManifest,
    PlatformExitClass,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    build_evidence_manifest,
)


class FactCodec(Protocol):
    """Explicit safe encoder/decoder for one registered FactKey kind."""

    schema_version: str

    def encode(self, fact: VersionedFact) -> dict[str, object]: ...

    def decode(self, payload: dict[str, object]) -> VersionedFact: ...


class _PostgresVersionedFactStore(VersionedFactStore):
    def __init__(self, unit_of_work: PostgresApplicationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def get(self, key: FactKey) -> VersionedFact | None:
        connection = self._unit_of_work._require_connection()
        codec = self._unit_of_work._codec(key.kind)
        row = await connection.fetchrow(
            """
            SELECT version, fact_id, codec_schema_version, payload_digest, payload
            FROM qep_versioned_fact_snapshots
            WHERE fact_kind = $1 AND fact_key = $2
            ORDER BY version DESC
            LIMIT 1
            """,
            key.kind,
            key.value,
        )
        if row is None:
            return None
        return _decode_fact(key=key, codec=codec, row=row)

    async def commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]:
        try:
            return await self._commit(command)
        except BaseException:
            self._unit_of_work._abort()
            raise

    async def _commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]:
        connection = self._unit_of_work._require_connection()
        codec = self._unit_of_work._codec(command.key.kind)
        if command.key.value != command.fact.id:
            _contract("fact_store", "key.value", "fact_id_mismatch")
        payload = _codec_payload(codec=codec, fact=command.fact)
        payload_digest = canonical_digest(schema_version=codec.schema_version, payload=payload)
        command_digest = _command_digest(command=command, payload_digest=payload_digest)
        replay = await connection.fetchrow(
            """
            SELECT request_digest, command_digest, response_status, response_ref,
                   fact_kind, fact_key, fact_version, fact_payload_digest
            FROM qep_versioned_fact_commands
            WHERE idempotency_scope = $1 AND idempotency_key = $2
            """,
            command.idempotency.scope,
            command.idempotency.key,
        )
        if replay is not None:
            if replay["request_digest"] != _digest_hex(command.idempotency.request_digest):
                raise IdempotencyConflict(
                    scope=command.idempotency.scope,
                    key=command.idempotency.key,
                    stored_digest=_digest(replay["request_digest"]),
                    received_digest=command.idempotency.request_digest,
                )
            if (
                replay["command_digest"] != _digest_hex(command_digest)
                or replay["response_status"] != command.idempotency.response_status
                or replay["response_ref"] != command.idempotency.response_ref
                or replay["fact_kind"] != command.key.kind
                or replay["fact_key"] != command.key.value
                or replay["fact_version"] != command.fact.version
                or replay["fact_payload_digest"] != _digest_hex(payload_digest)
            ):
                _contract("fact_store", "idempotency", "non_exact_replay")
            stored = await self.get(command.key)
            if stored is None or stored.version < command.fact.version:
                _contract("fact_store", "idempotency", "stored_result_missing")
            historical = await connection.fetchrow(
                """
                SELECT version, fact_id, codec_schema_version, payload_digest, payload
                FROM qep_versioned_fact_snapshots
                WHERE fact_kind = $1 AND fact_key = $2 AND version = $3
                """,
                command.key.kind,
                command.key.value,
                command.fact.version,
            )
            if historical is None:
                _contract("fact_store", "idempotency", "stored_result_missing")
            fact = _decode_fact(key=command.key, codec=codec, row=historical)
            return FactCommitResult(fact=cast(FactT, fact), replayed=True)

        current = await connection.fetchrow(
            """
            SELECT version, fact_id, codec_schema_version, payload_digest, payload
            FROM qep_versioned_fact_snapshots
            WHERE fact_kind = $1 AND fact_key = $2
            ORDER BY version DESC
            LIMIT 1
            FOR UPDATE
            """,
            command.key.kind,
            command.key.value,
        )
        if current is not None:
            _decode_fact(key=command.key, codec=codec, row=current)
        if command.expected_version is None:
            if current is not None:
                _contract("fact_store", "expected_version", "already_exists")
            required_version = 0
        else:
            if current is None:
                _contract("fact_store", "key", "not_found")
            current_version = cast(int, current["version"])
            if current_version != command.expected_version:
                raise VersionConflict(
                    entity_type=command.key.kind,
                    entity_id=command.key.value,
                    current_version=current_version,
                    expected_version=command.expected_version,
                )
            required_version = command.expected_version + 1
        if command.fact.version != required_version:
            _contract("fact_store", "fact.version", "not_next_version")

        status = await connection.execute(
            """
            INSERT INTO qep_versioned_fact_snapshots (
                fact_kind, fact_key, version, fact_id, codec_schema_version,
                payload_digest, payload, recorded_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, transaction_timestamp())
            """,
            command.key.kind,
            command.key.value,
            command.fact.version,
            command.fact.id,
            codec.schema_version,
            _digest_hex(payload_digest),
            _json(payload),
        )
        _require_inserted(status, resource="fact_store", field="fact")
        status = await connection.execute(
            """
            INSERT INTO qep_versioned_fact_commands (
                idempotency_scope, idempotency_key, request_digest, command_digest,
                response_status, response_ref, fact_kind, fact_key, fact_version,
                fact_payload_digest, recorded_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, transaction_timestamp())
            """,
            command.idempotency.scope,
            command.idempotency.key,
            _digest_hex(command.idempotency.request_digest),
            _digest_hex(command_digest),
            command.idempotency.response_status,
            command.idempotency.response_ref,
            command.key.kind,
            command.key.value,
            command.fact.version,
            _digest_hex(payload_digest),
        )
        _require_inserted(status, resource="fact_store", field="idempotency")
        return FactCommitResult(fact=command.fact, replayed=False)


class _PostgresEvidenceManifestIndex(EvidenceManifestIndex):
    def __init__(self, unit_of_work: PostgresApplicationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def get(self, attempt_id: str) -> EvidenceManifest | None:
        row = await self._unit_of_work._require_connection().fetchrow(
            """
            SELECT attempt_id, assignment_id, fence, schema_version, root_digest,
                   payload, finalized_at
            FROM qep_evidence_index
            WHERE attempt_id = $1
            """,
            attempt_id,
        )
        if row is None:
            return None
        return _decode_manifest(row)

    async def finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]:
        try:
            return await self._finalize(manifest)
        except BaseException:
            self._unit_of_work._abort()
            raise

    async def _finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]:
        payload = _manifest_payload(manifest)
        _decode_manifest_payload(payload)
        connection = self._unit_of_work._require_connection()
        stored = await self.get(manifest.attempt_id)
        if stored == manifest:
            return ReplayResult(value=stored, replayed=True)
        if stored is not None:
            raise EvidenceConflict(
                stored_root=stored.root_digest,
                received_root=manifest.root_digest,
            )
        status = await connection.execute(
            """
            INSERT INTO qep_evidence_index (
                id, attempt_id, assignment_id, fence, schema_version,
                root_digest, payload, finalized_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, transaction_timestamp())
            """,
            f"evidence-{_digest_hex(manifest.root_digest)}",
            manifest.attempt_id,
            manifest.assignment_id,
            manifest.fence,
            manifest.schema_version,
            _digest_hex(manifest.root_digest),
            _json(payload),
        )
        _require_inserted(status, resource="evidence_index", field="manifest")
        return ReplayResult(value=manifest, replayed=False)


class _PostgresAuditLog(AuditLog):
    def __init__(self, unit_of_work: PostgresApplicationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def append(self, record: AuditRecord) -> ReplayResult[AuditRecord]:
        try:
            return await self._append(record)
        except BaseException:
            self._unit_of_work._abort()
            raise

    async def _append(self, record: AuditRecord) -> ReplayResult[AuditRecord]:
        connection = self._unit_of_work._require_connection()
        stored = await connection.fetchrow(
            """
            SELECT id, actor_id, action, object_type, object_id, decision,
                   reason_code, before_digest, after_digest, payload, occurred_at
            FROM qep_audit_events
            WHERE id = $1
            """,
            record.event_id,
        )
        if stored is not None:
            decoded = _decode_audit(stored)
            if decoded == record:
                return ReplayResult(value=decoded, replayed=True)
            _contract("audit_log", "event_id", "conflicting_content")
        status = await connection.execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            record.event_id,
            record.actor_id,
            record.action,
            record.object_kind,
            record.object_id,
            record.decision,
            record.reason,
            _optional_digest_hex(record.before_digest),
            _optional_digest_hex(record.after_digest),
            _json(_audit_payload(record)),
            record.occurred_at,
        )
        _require_inserted(status, resource="audit_log", field="record")
        return ReplayResult(value=record, replayed=False)

    async def list_for_object(self, object_kind: str, object_id: str) -> tuple[AuditRecord, ...]:
        rows = await self._unit_of_work._require_connection().fetch(
            """
            SELECT id, actor_id, action, object_type, object_id, decision,
                   reason_code, before_digest, after_digest, payload, occurred_at
            FROM qep_audit_events
            WHERE object_type = $1 AND object_id = $2
              AND payload ->> 'schema_version' = 'qep.application-audit.v1'
            ORDER BY occurred_at, id
            """,
            object_kind,
            object_id,
        )
        return tuple(_decode_audit(row) for row in rows)


class PostgresApplicationUnitOfWork:
    """One-shot caller-owned transaction implementing ApplicationUnitOfWork."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        fact_codecs: Mapping[str, FactCodec],
    ) -> None:
        self._pool = pool
        self._fact_codecs = dict(fact_codecs)
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._facts = _PostgresVersionedFactStore(self)
        self._evidence = _PostgresEvidenceManifestIndex(self)
        self._audit = _PostgresAuditLog(self)
        self._aborted = False
        self._closed = False

    @property
    def facts(self) -> VersionedFactStore:
        self._require_connection()
        return self._facts

    @property
    def evidence(self) -> EvidenceManifestIndex:
        self._require_connection()
        return self._evidence

    @property
    def audit(self) -> AuditLog:
        self._require_connection()
        return self._audit

    async def __aenter__(self) -> Self:
        if self._closed:
            self._state_error("closed")
        if self._connection is not None:
            self._state_error("already_active")
        connection = await self._pool.acquire()
        try:
            transaction = connection.transaction(isolation="read_committed")
            await transaction.start()
        except BaseException:
            self._closed = True
            await self._pool.release(connection)
            raise
        self._connection = connection
        self._transaction = transaction
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._closed:
            await self.rollback()

    async def commit(self) -> None:
        transaction, connection = self._active_transaction()
        try:
            await transaction.commit()
        except BaseException:
            self._aborted = True
            try:
                await transaction.rollback()
            finally:
                await self._close(connection)
            raise
        await self._close(connection)

    async def rollback(self) -> None:
        if self._closed:
            return
        connection = self._connection
        transaction = self._transaction
        if connection is None or transaction is None:
            self._state_error("not_active")
        try:
            await transaction.rollback()
        finally:
            await self._close(connection)

    def _codec(self, kind: str) -> FactCodec:
        codec = self._fact_codecs.get(kind)
        if codec is None:
            _contract("fact_store", "key.kind", "unsupported")
        if not isinstance(codec.schema_version, str) or not codec.schema_version.strip():
            _contract("fact_store", "codec", "invalid")
        return codec

    def _require_connection(self) -> asyncpg.Connection:
        if self._closed:
            self._state_error("closed")
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active")
        return self._connection

    def _active_transaction(self) -> tuple[asyncpg.Transaction, asyncpg.Connection]:
        connection = self._require_connection()
        return cast("asyncpg.Transaction", self._transaction), connection

    def _abort(self) -> None:
        self._aborted = True

    async def _close(self, connection: asyncpg.Connection) -> None:
        self._transaction = None
        self._connection = None
        self._closed = True
        await self._pool.release(connection)

    @staticmethod
    def _state_error(reason: str) -> None:
        raise PortContractError(resource="unit_of_work", field="state", reason=reason)


def _codec_payload(*, codec: FactCodec, fact: VersionedFact) -> dict[str, object]:
    payload = codec.encode(fact)
    if not isinstance(payload, dict):
        _contract("fact_store", "codec", "invalid_payload")
    decoded = codec.decode(payload)
    if decoded != fact:
        _contract("fact_store", "codec", "round_trip_mismatch")
    canonical_digest(schema_version=codec.schema_version, payload=cast(JsonValue, payload))
    return payload


def _decode_fact(*, key: FactKey, codec: FactCodec, row: asyncpg.Record) -> VersionedFact:
    try:
        payload = _json_object(row["payload"])
        payload_digest = canonical_digest(
            schema_version=codec.schema_version,
            payload=cast(JsonValue, payload),
        )
        fact = codec.decode(payload)
        if (
            row["fact_id"] != key.value
            or row["codec_schema_version"] != codec.schema_version
            or row["payload_digest"] != _digest_hex(payload_digest)
            or fact.id != key.value
            or fact.version != row["version"]
            or codec.encode(fact) != payload
        ):
            _contract("fact_store", "stored_fact", "integrity_invalid")
        return fact
    except PortContractError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PortContractError(
            resource="fact_store", field="stored_fact", reason="integrity_invalid"
        ) from error


def _command_digest(
    *, command: VersionedFactCommand[VersionedFact], payload_digest: Digest
) -> Digest:
    return canonical_digest(
        schema_version="qep.versioned-fact-command.v1",
        payload={
            "fact_kind": command.key.kind,
            "fact_key": command.key.value,
            "expected_version": command.expected_version,
            "fact_version": command.fact.version,
            "fact_payload_digest": payload_digest.value,
            "response_status": command.idempotency.response_status,
            "response_ref": command.idempotency.response_ref,
        },
    )


def _manifest_payload(manifest: EvidenceManifest) -> dict[str, object]:
    _validate_manifest_shape(manifest)
    return {
        "attempt_id": manifest.attempt_id,
        "run_id": manifest.run_id,
        "attempt_no": manifest.attempt_no,
        "assignment_id": manifest.assignment_id,
        "fence": manifest.fence,
        "worker": {
            "worker_id": manifest.worker.worker_id,
            "generation": manifest.worker.generation,
        },
        "execution_spec_digest": manifest.execution_spec_digest.value,
        "platform_exit": {
            "source_event_id": manifest.platform_exit.source_event_id,
            "pid": manifest.platform_exit.pid,
            "exit_class": manifest.platform_exit.exit_class.value,
            "exit_code": manifest.platform_exit.exit_code,
            "signal": manifest.platform_exit.signal,
            "oom": manifest.platform_exit.oom,
            "timeout": manifest.platform_exit.timeout,
        },
        "case_summary": _case_summary_payload(manifest.case_summary),
        "artifacts": [
            {
                "path": artifact.path.value,
                "content_class": artifact.content_class.value,
                "size_bytes": artifact.size_bytes,
                "digest": artifact.digest.value,
            }
            for artifact in manifest.artifacts
        ],
        "cancellation_stop": _cancellation_stop_payload(manifest.cancellation_stop),
        "classification_version": manifest.classification_version,
        "outcome": manifest.outcome.value,
        "root_digest": manifest.root_digest.value,
    }


def _decode_manifest(row: asyncpg.Record) -> EvidenceManifest:
    try:
        payload = _json_object(row["payload"])
        manifest = _decode_manifest_payload(payload)
        if (
            row["attempt_id"] != manifest.attempt_id
            or row["assignment_id"] != manifest.assignment_id
            or row["fence"] != manifest.fence
            or row["schema_version"] != manifest.schema_version
            or row["root_digest"] != _digest_hex(manifest.root_digest)
        ):
            _contract("evidence_index", "stored_manifest", "integrity_invalid")
        return manifest
    except PortContractError as error:
        raise PortContractError(
            resource="evidence_index", field="stored_manifest", reason="integrity_invalid"
        ) from error
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PortContractError(
            resource="evidence_index", field="stored_manifest", reason="integrity_invalid"
        ) from error


def _decode_manifest_payload(payload: dict[str, object]) -> EvidenceManifest:
    worker = cast(dict[str, object], payload["worker"])
    exit_payload = cast(dict[str, object], payload["platform_exit"])
    case_payload = cast(dict[str, object] | None, payload["case_summary"])
    artifacts_payload = cast(list[dict[str, object]], payload["artifacts"])
    stop_payload = cast(dict[str, object] | None, payload["cancellation_stop"])
    cancellation_stop = None
    if stop_payload is not None:
        cancellation_stop = TrustedCancellationStop(
            run_id=cast(str, payload["run_id"]),
            attempt_id=cast(str, payload["attempt_id"]),
            fence=cast(int, payload["fence"]),
            worker=WorkerRef(
                worker_id=cast(str, worker["worker_id"]),
                generation=cast(int, worker["generation"]),
            ),
            cancellation_intent_digest=Digest(
                cast(str, stop_payload["cancellation_intent_digest"])
            ),
            source_event_id=cast(str, stop_payload["source_event_id"]),
            process_stopped_at=_parse_datetime(stop_payload["process_stopped_at"]),
            sut_access_stopped_at=_parse_datetime(stop_payload["sut_access_stopped_at"]),
            recorded_at=_parse_datetime(stop_payload["recorded_at"]),
        )
        if stop_payload["proof_digest"] != cancellation_stop.digest.value:
            _contract("evidence_index", "manifest", "not_domain_built")
    case_summary = None
    if case_payload is not None:
        case_summary = ValidatedCaseSummary(
            schema_version=cast(str, case_payload["schema_version"]),
            source_artifact_path=ArtifactPath(cast(str, case_payload["source_artifact_path"])),
            source_artifact_digest=Digest(cast(str, case_payload["source_artifact_digest"])),
            expected=cast(int, case_payload["expected"]),
            passed=cast(int, case_payload["passed"]),
            failed=cast(int, case_payload["failed"]),
            skipped=cast(int, case_payload["skipped"]),
            not_reported=cast(int, case_payload["not_reported"]),
            unexpected=cast(int, case_payload["unexpected"]),
        )
    manifest = build_evidence_manifest(
        attempt_id=cast(str, payload["attempt_id"]),
        run_id=cast(str, payload["run_id"]),
        attempt_no=cast(int, payload["attempt_no"]),
        assignment_id=cast(str, payload["assignment_id"]),
        fence=cast(int, payload["fence"]),
        worker=WorkerRef(
            worker_id=cast(str, worker["worker_id"]),
            generation=cast(int, worker["generation"]),
        ),
        execution_spec_digest=Digest(cast(str, payload["execution_spec_digest"])),
        trusted_exit=TrustedExitFacts(
            source_event_id=cast(str, exit_payload["source_event_id"]),
            pid=cast(int | None, exit_payload["pid"]),
            exit_class=PlatformExitClass(cast(str, exit_payload["exit_class"])),
            exit_code=cast(int | None, exit_payload["exit_code"]),
            signal=cast(int | None, exit_payload["signal"]),
            oom=cast(bool, exit_payload["oom"]),
            timeout=cast(bool, exit_payload["timeout"]),
        ),
        case_summary=case_summary,
        artifacts=tuple(
            VerifiedArtifact(
                path=ArtifactPath(cast(str, item["path"])),
                content_class=ArtifactClass(cast(str, item["content_class"])),
                size_bytes=cast(int, item["size_bytes"]),
                digest=Digest(cast(str, item["digest"])),
            )
            for item in artifacts_payload
        ),
        cancellation_stop=cancellation_stop,
    )
    _validate_manifest_shape(manifest)
    if (
        payload["classification_version"] != manifest.classification_version
        or payload["outcome"] != manifest.outcome.value
        or payload["root_digest"] != manifest.root_digest.value
        or payload != _manifest_payload(manifest)
    ):
        _contract("evidence_index", "manifest", "not_domain_built")
    return manifest


def _validate_manifest_shape(manifest: object) -> None:
    valid = (
        isinstance(manifest, EvidenceManifest)
        and _nonempty_string(manifest.attempt_id)
        and _nonempty_string(manifest.run_id)
        and _positive_integer(manifest.attempt_no)
        and _nonempty_string(manifest.assignment_id)
        and _positive_integer(manifest.fence)
        and isinstance(manifest.worker, WorkerRef)
        and isinstance(manifest.execution_spec_digest, Digest)
        and _valid_exit(manifest.platform_exit)
        and _valid_case_summary(manifest.case_summary)
        and isinstance(manifest.artifacts, tuple)
        and all(_valid_artifact(artifact) for artifact in manifest.artifacts)
        and (
            manifest.cancellation_stop is None
            or isinstance(manifest.cancellation_stop, TrustedCancellationStop)
        )
    )
    if not valid:
        _contract("evidence_index", "manifest", "not_domain_built")


def _valid_exit(value: object) -> bool:
    return (
        isinstance(value, TrustedExitFacts)
        and _nonempty_string(value.source_event_id)
        and _optional_integer(value.pid)
        and isinstance(value.exit_class, PlatformExitClass)
        and _optional_integer(value.exit_code)
        and _optional_integer(value.signal)
        and isinstance(value.oom, bool)
        and isinstance(value.timeout, bool)
    )


def _valid_case_summary(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, ValidatedCaseSummary):
        return False
    return (
        _nonempty_string(value.schema_version)
        and isinstance(value.source_artifact_path, ArtifactPath)
        and _nonempty_string(value.source_artifact_path.value)
        and isinstance(value.source_artifact_digest, Digest)
        and all(
            _nonnegative_integer(count)
            for count in (
                value.expected,
                value.passed,
                value.failed,
                value.skipped,
                value.not_reported,
                value.unexpected,
            )
        )
    )


def _valid_artifact(value: object) -> bool:
    return (
        isinstance(value, VerifiedArtifact)
        and isinstance(value.path, ArtifactPath)
        and _nonempty_string(value.path.value)
        and isinstance(value.content_class, ArtifactClass)
        and _nonnegative_integer(value.size_bytes)
        and isinstance(value.digest, Digest)
    )


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value: object) -> bool:
    return _integer(value) and value > 0


def _nonnegative_integer(value: object) -> bool:
    return _integer(value) and value >= 0


def _optional_integer(value: object) -> bool:
    return value is None or _integer(value)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _case_summary_payload(value: ValidatedCaseSummary | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "schema_version": value.schema_version,
        "source_artifact_path": value.source_artifact_path.value,
        "source_artifact_digest": value.source_artifact_digest.value,
        "expected": value.expected,
        "passed": value.passed,
        "failed": value.failed,
        "skipped": value.skipped,
        "not_reported": value.not_reported,
        "unexpected": value.unexpected,
    }


def _cancellation_stop_payload(
    value: TrustedCancellationStop | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "proof_digest": value.digest.value,
        "cancellation_intent_digest": value.cancellation_intent_digest.value,
        "source_event_id": value.source_event_id,
        "process_stopped_at": value.process_stopped_at.isoformat().replace("+00:00", "Z"),
        "sut_access_stopped_at": value.sut_access_stopped_at.isoformat().replace("+00:00", "Z"),
        "recorded_at": value.recorded_at.isoformat().replace("+00:00", "Z"),
    }


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _audit_payload(record: AuditRecord) -> dict[str, object]:
    return {
        "schema_version": "qep.application-audit.v1",
        "authentication_strength": record.authentication_strength,
        "request_id": record.request_id,
        "source": record.source,
    }


def _decode_audit(row: asyncpg.Record) -> AuditRecord:
    try:
        payload = _json_object(row["payload"])
        if payload.get("schema_version") != "qep.application-audit.v1":
            _contract("audit_log", "stored_record", "integrity_invalid")
        record = AuditRecord(
            event_id=row["id"],
            actor_id=row["actor_id"],
            authentication_strength=cast(str, payload["authentication_strength"]),
            request_id=cast(str, payload["request_id"]),
            object_kind=row["object_type"],
            object_id=row["object_id"],
            action=row["action"],
            decision=row["decision"],
            reason=row["reason_code"],
            before_digest=_optional_digest(row["before_digest"]),
            after_digest=_optional_digest(row["after_digest"]),
            occurred_at=cast(datetime, row["occurred_at"]),
            source=cast(str, payload["source"]),
        )
        if payload != _audit_payload(record):
            _contract("audit_log", "stored_record", "integrity_invalid")
        return record
    except PortContractError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PortContractError(
            resource="audit_log", field="stored_record", reason="integrity_invalid"
        ) from error


def _json_object(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise ValueError("stored payload must be an object")
    return decoded


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_inserted(status: str, *, resource: str, field: str) -> None:
    if status != "INSERT 0 1":
        _contract(resource, field, "write_missing")


def _contract(resource: str, field: str, reason: str) -> None:
    raise PortContractError(resource=resource, field=field, reason=reason)


def _digest(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _optional_digest_hex(value: Digest | None) -> str | None:
    return None if value is None else _digest_hex(value)


def _optional_digest(value: str | None) -> Digest | None:
    return None if value is None else _digest(value)
