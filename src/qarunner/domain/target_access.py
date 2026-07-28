"""Target Spec, Grant, and fail-closed egress decisions (M5 foundation).

Target Spec is admin-maintained vocabulary (DES §8.1). Users never supply
arbitrary URLs as authorization. Target Grant is the permission ceiling
(DES §8.2). ``evaluate_egress_request`` is the pure decision function an
Egress Gateway (or same-host enforcer) must call per hop — including every
HTTP redirect hop (T-M5-DNS-001 / T-M5-EGRESS-001).

This module intentionally has no network I/O: DNS resolution results are
inputs, not side effects, so tests can pin addresses without inventing a
resolver.
"""

from __future__ import annotations

import enum
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError


class TargetGrantState(enum.StrEnum):
    """Grant lifecycle — only ACTIVE inside validity is effective."""

    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EgressDecision(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class EgressDenyReason(enum.StrEnum):
    GRANT_MISSING = "grant_missing"
    GRANT_NOT_EFFECTIVE = "grant_not_effective"
    DNS_NOT_IN_GRANT = "dns_not_in_grant"
    PORT_NOT_IN_GRANT = "port_not_in_grant"
    PROTOCOL_NOT_IN_GRANT = "protocol_not_in_grant"
    FORBIDDEN_METADATA = "forbidden_metadata"
    FORBIDDEN_LOOPBACK = "forbidden_loopback"
    FORBIDDEN_LINK_LOCAL = "forbidden_link_local"
    PRIVATE_NETWORK_NOT_ALLOWED = "private_network_not_allowed"
    INVALID_RESOLVED_IP = "invalid_resolved_ip"


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """Admin-maintained authorized destination vocabulary for one target."""

    target_id: str
    environment: str
    allowed_dns_names: tuple[str, ...]
    allowed_protocols: tuple[str, ...]
    allowed_ports: tuple[int, ...]
    allow_private_rfc1918: bool
    owner_id: str

    def __post_init__(self) -> None:
        entity = "target_spec"
        for field in ("target_id", "environment", "owner_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
        if (
            not isinstance(self.allowed_dns_names, tuple)
            or not self.allowed_dns_names
            or any(
                not isinstance(name, str) or not name.strip() for name in self.allowed_dns_names
            )
        ):
            raise DomainValidationError(
                entity_type=entity, field="allowed_dns_names", reason="invalid"
            )
        if (
            not isinstance(self.allowed_protocols, tuple)
            or not self.allowed_protocols
            or any(
                not isinstance(proto, str) or not proto.strip() for proto in self.allowed_protocols
            )
        ):
            raise DomainValidationError(
                entity_type=entity, field="allowed_protocols", reason="invalid"
            )
        if (
            not isinstance(self.allowed_ports, tuple)
            or not self.allowed_ports
            or any(
                isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535
                for port in self.allowed_ports
            )
        ):
            raise DomainValidationError(
                entity_type=entity, field="allowed_ports", reason="invalid"
            )
        if not isinstance(self.allow_private_rfc1918, bool):
            raise DomainValidationError(
                entity_type=entity, field="allow_private_rfc1918", reason="not_bool"
            )

    @property
    def target_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.target-spec.v1",
            payload={
                "target_id": self.target_id,
                "environment": self.environment,
                "allowed_dns_names": list(self.allowed_dns_names),
                "allowed_protocols": list(self.allowed_protocols),
                "allowed_ports": list(self.allowed_ports),
                "allow_private_rfc1918": self.allow_private_rfc1918,
                "owner_id": self.owner_id,
            },
        )


@dataclass(frozen=True, slots=True)
class TargetGrant:
    """Permission ceiling for one TargetSpec over a validity window."""

    grant_id: str
    target: TargetSpec
    state: TargetGrantState
    max_concurrent_units: int
    valid_from: datetime
    valid_until: datetime
    version: int

    def __post_init__(self) -> None:
        entity = "target_grant"
        if not isinstance(self.grant_id, str) or not self.grant_id.strip():
            raise DomainValidationError(entity_type=entity, field="grant_id", reason="invalid")
        if not isinstance(self.target, TargetSpec):
            raise DomainValidationError(
                entity_type=entity, field="target", reason="not_target_spec"
            )
        if not isinstance(self.state, TargetGrantState):
            raise DomainValidationError(entity_type=entity, field="state", reason="invalid")
        if (
            isinstance(self.max_concurrent_units, bool)
            or not isinstance(self.max_concurrent_units, int)
            or self.max_concurrent_units < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="max_concurrent_units", reason="invalid"
            )
        for field in ("valid_from", "valid_until"):
            value = getattr(self, field)
            if not isinstance(value, datetime):
                raise DomainValidationError(entity_type=entity, field=field, reason="not_datetime")
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise DomainValidationError(entity_type=entity, field=field, reason="not_utc")
        if self.valid_until <= self.valid_from:
            raise DomainValidationError(
                entity_type=entity, field="valid_until", reason="not_after_valid_from"
            )
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise DomainValidationError(entity_type=entity, field="version", reason="invalid")

    def is_effective_at(self, observed_at: datetime) -> bool:
        if not isinstance(observed_at, datetime):
            raise DomainValidationError(
                entity_type="target_grant", field="observed_at", reason="not_datetime"
            )
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise DomainValidationError(
                entity_type="target_grant", field="observed_at", reason="not_utc"
            )
        if self.state is not TargetGrantState.ACTIVE:
            return False
        return self.valid_from <= observed_at < self.valid_until

    @property
    def grant_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.target-grant.v1",
            payload={
                "grant_id": self.grant_id,
                "target_digest": self.target.target_digest.value,
                "state": self.state.value,
                "max_concurrent_units": self.max_concurrent_units,
                "valid_from": self.valid_from.isoformat(),
                "valid_until": self.valid_until.isoformat(),
                "version": self.version,
            },
        )


@dataclass(frozen=True, slots=True)
class EgressEvaluation:
    """Audit-ready allow/deny for one destination hop."""

    allowed: bool
    decision: EgressDecision
    reason: EgressDenyReason | None
    host: str
    port: int
    protocol: str
    resolved_ip: str
    is_redirect_hop: bool
    grant_id: str | None
    target_id: str | None


def evaluate_egress_request(
    *,
    grant: TargetGrant | None,
    host: str,
    port: int,
    protocol: str,
    resolved_ip: str,
    observed_at: datetime,
    is_redirect_hop: bool,
) -> EgressEvaluation:
    """Fail-closed egress decision for one hop (including redirect hops).

    Order (first match wins on the deny side):

    1. grant missing / not effective
    2. forbidden address classes (metadata / loopback / link-local)
    3. private RFC1918 unless TargetSpec.allow_private_rfc1918
    4. host must match an allowed DNS pattern (IP literals never match)
    5. protocol / port must be in the Grant's TargetSpec
    """
    entity = "egress_request"
    if not isinstance(host, str) or not host.strip():
        raise DomainValidationError(entity_type=entity, field="host", reason="invalid")
    if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
        raise DomainValidationError(entity_type=entity, field="port", reason="invalid")
    if not isinstance(protocol, str) or not protocol.strip():
        raise DomainValidationError(entity_type=entity, field="protocol", reason="invalid")
    if not isinstance(resolved_ip, str) or not resolved_ip.strip():
        raise DomainValidationError(entity_type=entity, field="resolved_ip", reason="invalid")
    if not isinstance(observed_at, datetime):
        raise DomainValidationError(entity_type=entity, field="observed_at", reason="not_datetime")
    if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
        raise DomainValidationError(entity_type=entity, field="observed_at", reason="not_utc")
    if not isinstance(is_redirect_hop, bool):
        raise DomainValidationError(entity_type=entity, field="is_redirect_hop", reason="not_bool")
    if grant is not None and not isinstance(grant, TargetGrant):
        raise DomainValidationError(entity_type=entity, field="grant", reason="not_target_grant")

    host_norm = host.strip().lower().rstrip(".")
    protocol_norm = protocol.strip().lower()

    def _deny(reason: EgressDenyReason) -> EgressEvaluation:
        return EgressEvaluation(
            allowed=False,
            decision=EgressDecision.DENY,
            reason=reason,
            host=host_norm,
            port=port,
            protocol=protocol_norm,
            resolved_ip=resolved_ip.strip(),
            is_redirect_hop=is_redirect_hop,
            grant_id=None if grant is None else grant.grant_id,
            target_id=None if grant is None else grant.target.target_id,
        )

    if grant is None:
        return _deny(EgressDenyReason.GRANT_MISSING)
    if not grant.is_effective_at(observed_at):
        return _deny(EgressDenyReason.GRANT_NOT_EFFECTIVE)

    try:
        addr = ipaddress.ip_address(resolved_ip.strip())
    except ValueError:
        return _deny(EgressDenyReason.INVALID_RESOLVED_IP)

    if _is_cloud_metadata(addr):
        return _deny(EgressDenyReason.FORBIDDEN_METADATA)
    if addr.is_loopback:
        return _deny(EgressDenyReason.FORBIDDEN_LOOPBACK)
    if addr.is_link_local:
        return _deny(EgressDenyReason.FORBIDDEN_LINK_LOCAL)
    if addr.is_private and not grant.target.allow_private_rfc1918:
        return _deny(EgressDenyReason.PRIVATE_NETWORK_NOT_ALLOWED)

    if not _host_matches_grant(host_norm, grant.target.allowed_dns_names):
        return _deny(EgressDenyReason.DNS_NOT_IN_GRANT)
    if protocol_norm not in {p.lower() for p in grant.target.allowed_protocols}:
        return _deny(EgressDenyReason.PROTOCOL_NOT_IN_GRANT)
    if port not in grant.target.allowed_ports:
        return _deny(EgressDenyReason.PORT_NOT_IN_GRANT)

    return EgressEvaluation(
        allowed=True,
        decision=EgressDecision.ALLOW,
        reason=None,
        host=host_norm,
        port=port,
        protocol=protocol_norm,
        resolved_ip=resolved_ip.strip(),
        is_redirect_hop=is_redirect_hop,
        grant_id=grant.grant_id,
        target_id=grant.target.target_id,
    )


def _is_cloud_metadata(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """AWS/GCP/Azure classic metadata endpoint and IPv6 equivalent."""
    return (
        isinstance(addr, ipaddress.IPv4Address)
        and addr == ipaddress.IPv4Address("169.254.169.254")
    ) or (
        isinstance(addr, ipaddress.IPv6Address) and addr == ipaddress.IPv6Address("fd00:ec2::254")
    )


def _host_matches_grant(host: str, patterns: tuple[str, ...]) -> bool:
    # IP literals are never DNS names — T-M5-DNS-001 direct-IP bypass ban.
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    for raw in patterns:
        pattern = raw.strip().lower().rstrip(".")
        # TargetSpec construction already rejects blank DNS names; kept as
        # defense-in-depth if a future caller feeds raw pattern tuples.
        if not pattern:  # pragma: no cover
            continue
        if pattern.startswith("*.") and len(pattern) > 2:
            # "*.cdn.example.com" matches "a.cdn.example.com" and
            # "a.b.cdn.example.com", but not "cdn.example.com" itself and not
            # "cdn.example.com.evil.com".
            suffix = pattern[1:]  # ".cdn.example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
            continue
        if host == pattern:
            return True
    return False
