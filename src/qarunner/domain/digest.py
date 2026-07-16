"""Canonical content digests for greenfield execution contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from qarunner.domain.errors import CanonicalizationError, DomainValidationError

type JsonValue = None | bool | int | str | list[JsonValue] | dict[str, JsonValue]

_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class Digest:
    """A versioned contract's lowercase SHA-256 content digest."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError(
                entity_type="digest",
                field="value",
                reason="not_string",
            )
        if _SHA256_PATTERN.fullmatch(self.value) is None:
            raise DomainValidationError(
                entity_type="digest",
                field="value",
                reason="invalid_sha256",
            )


def canonical_digest(*, schema_version: str, payload: JsonValue) -> Digest:
    """Hash a schema-versioned JSON payload using a stable representation."""
    if not isinstance(schema_version, str):
        raise CanonicalizationError(
            path="$.schema_version",
            reason="schema version must be a string",
        )
    _validate_json_value(schema_version, path="$.schema_version")
    _validate_json_value(payload, path="$.payload")
    canonical = json.dumps(
        {"payload": payload, "schema_version": schema_version},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return Digest(value=f"sha256:{hashlib.sha256(canonical).hexdigest()}")


def canonical_materialized_run_set_digest(*, batch_id: str, run_ids: tuple[str, ...]) -> Digest:
    """Bind the stable authoritative Run set observed under Batch serialization."""
    return canonical_digest(
        schema_version="qep.authoritative-materialized-run-set.v1",
        payload={"batch_id": batch_id, "run_ids": list(sorted(run_ids))},
    )


def _validate_json_value(value: object, *, path: str) -> None:
    """Validate the deterministic I-JSON subset used by M0 contracts."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise CanonicalizationError(path=path, reason="integer exceeds I-JSON safe range")
        return
    if isinstance(value, float):
        raise CanonicalizationError(path=path, reason="floating point values are not supported")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise CanonicalizationError(path=path, reason="string is not valid Unicode") from error
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise CanonicalizationError(path=path, reason="object keys must be strings")
        for key, item in value.items():
            _validate_json_value(key, path=f"{path}.<key>")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise CanonicalizationError(path=path, reason=f"unsupported type: {type(value).__name__}")
