"""T-M5-EGRESS-001 / T-M5-DNS-001 foundation: Target Spec + Grant + fail-closed egress.

Admin-maintained TargetSpec is the only authorized destination vocabulary.
Users never feed arbitrary URLs as authority. Grant is the permission ceiling;
egress decisions are fail-closed without an active Grant covering the hop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    DomainValidationError,
    EgressDecision,
    EgressDenyReason,
    TargetGrant,
    TargetGrantState,
    TargetSpec,
    evaluate_egress_request,
)

T0 = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)


def _spec(**changes) -> TargetSpec:
    values = {
        "target_id": "target-staging-web",
        "environment": "staging",
        "allowed_dns_names": ("app.staging.example.com", "*.cdn.staging.example.com"),
        "allowed_protocols": ("https",),
        "allowed_ports": (443,),
        "allow_private_rfc1918": False,
        "owner_id": "owner-001",
    }
    values.update(changes)
    return TargetSpec(**values)


def _grant(*, spec: TargetSpec | None = None, **changes) -> TargetGrant:
    target = spec if spec is not None else _spec()
    values = {
        "grant_id": "grant-001",
        "target": target,
        "state": TargetGrantState.ACTIVE,
        "max_concurrent_units": 2,
        "valid_from": T0,
        "valid_until": T0 + timedelta(hours=2),
        "version": 1,
    }
    values.update(changes)
    return TargetGrant(**values)


# ── TargetSpec ───────────────────────────────────────────────────────────────


def test_target_spec_digest_is_stable_and_binds_semantic_fields() -> None:
    a = _spec()
    b = _spec()
    assert a.target_digest == b.target_digest
    assert _spec(allowed_ports=(443, 8443)).target_digest != a.target_digest
    assert _spec(allowed_dns_names=("other.example.com",)).target_digest != a.target_digest


def test_target_spec_rejects_empty_dns_or_ports() -> None:
    with pytest.raises(DomainValidationError):
        _spec(allowed_dns_names=())
    with pytest.raises(DomainValidationError):
        _spec(allowed_ports=())
    with pytest.raises(DomainValidationError):
        _spec(allowed_protocols=())


def test_target_spec_rejects_blank_ids() -> None:
    with pytest.raises(DomainValidationError):
        _spec(target_id=" ")
    with pytest.raises(DomainValidationError):
        _spec(environment="")


# ── TargetGrant lifecycle ────────────────────────────────────────────────────


def test_grant_active_only_inside_validity_window() -> None:
    grant = _grant()
    assert grant.is_effective_at(T0 + timedelta(minutes=30)) is True
    assert grant.is_effective_at(T0 - timedelta(seconds=1)) is False
    assert grant.is_effective_at(T0 + timedelta(hours=3)) is False


def test_grant_suspended_or_revoked_is_never_effective() -> None:
    for state in (TargetGrantState.SUSPENDED, TargetGrantState.REVOKED, TargetGrantState.EXPIRED):
        grant = _grant(state=state)
        assert grant.is_effective_at(T0 + timedelta(minutes=1)) is False


def test_grant_draft_or_approved_is_not_yet_effective() -> None:
    for state in (TargetGrantState.DRAFT, TargetGrantState.APPROVED):
        grant = _grant(state=state)
        assert grant.is_effective_at(T0 + timedelta(minutes=1)) is False


def test_grant_digest_binds_target_and_limits() -> None:
    g1 = _grant()
    g2 = _grant(max_concurrent_units=5)
    assert g1.grant_digest != g2.grant_digest
    assert _grant(target=_spec(target_id="other")).grant_digest != g1.grant_digest


# ── Egress decisions (fail closed) ───────────────────────────────────────────


def test_active_grant_allows_exact_approved_https_host() -> None:
    decision = evaluate_egress_request(
        grant=_grant(),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is True
    assert decision.decision is EgressDecision.ALLOW
    assert decision.reason is None


def test_wildcard_dns_allows_matching_subdomain_only() -> None:
    grant = _grant()
    ok = evaluate_egress_request(
        grant=grant,
        host="assets.cdn.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.11",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert ok.allowed is True
    bad = evaluate_egress_request(
        grant=grant,
        host="cdn.staging.example.com.evil.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.12",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert bad.allowed is False
    assert bad.reason is EgressDenyReason.DNS_NOT_IN_GRANT


def test_missing_or_ineffective_grant_denies() -> None:
    decision = evaluate_egress_request(
        grant=None,
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is False
    assert decision.reason is EgressDenyReason.GRANT_MISSING

    revoked = evaluate_egress_request(
        grant=_grant(state=TargetGrantState.REVOKED),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert revoked.reason is EgressDenyReason.GRANT_NOT_EFFECTIVE


def test_unapproved_port_or_protocol_denied() -> None:
    grant = _grant()
    port = evaluate_egress_request(
        grant=grant,
        host="app.staging.example.com",
        port=80,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert port.reason is EgressDenyReason.PORT_NOT_IN_GRANT
    proto = evaluate_egress_request(
        grant=grant,
        host="app.staging.example.com",
        port=443,
        protocol="http",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert proto.reason is EgressDenyReason.PROTOCOL_NOT_IN_GRANT


def test_metadata_loopback_and_link_local_always_denied() -> None:
    grant = _grant(target=_spec(allow_private_rfc1918=True))
    for ip, reason in (
        ("169.254.169.254", EgressDenyReason.FORBIDDEN_METADATA),
        ("127.0.0.1", EgressDenyReason.FORBIDDEN_LOOPBACK),
        ("169.254.1.1", EgressDenyReason.FORBIDDEN_LINK_LOCAL),
        ("::1", EgressDenyReason.FORBIDDEN_LOOPBACK),
    ):
        decision = evaluate_egress_request(
            grant=grant,
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip=ip,
            observed_at=T0 + timedelta(minutes=5),
            is_redirect_hop=False,
        )
        assert decision.allowed is False, ip
        assert decision.reason is reason, ip


def test_private_rfc1918_denied_unless_explicitly_allowed() -> None:
    denied = evaluate_egress_request(
        grant=_grant(),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="10.0.0.8",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert denied.reason is EgressDenyReason.PRIVATE_NETWORK_NOT_ALLOWED

    allowed = evaluate_egress_request(
        grant=_grant(target=_spec(allow_private_rfc1918=True)),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="10.0.0.8",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert allowed.allowed is True


def test_direct_ip_host_cannot_bypass_dns_grant() -> None:
    """T-M5-DNS-001: dialing an IP literal is not an approved DNS name."""
    decision = evaluate_egress_request(
        grant=_grant(),
        host="93.184.216.10",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is False
    assert decision.reason is EgressDenyReason.DNS_NOT_IN_GRANT


def test_redirect_hop_must_be_reauthorized_against_same_grant() -> None:
    """Every redirect hop re-runs the full decision — no sticky allow from hop 0."""
    grant = _grant()
    first = evaluate_egress_request(
        grant=grant,
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert first.allowed is True
    second = evaluate_egress_request(
        grant=grant,
        host="evil.example.net",
        port=443,
        protocol="https",
        resolved_ip="93.184.216.99",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=True,
    )
    assert second.allowed is False
    assert second.reason is EgressDenyReason.DNS_NOT_IN_GRANT
    assert second.is_redirect_hop is True


def test_evaluate_rejects_untyped_inputs() -> None:
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="",
            port=443,
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at=T0,
            is_redirect_hop=False,
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=True,  # type: ignore[arg-type]
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at=T0,
            is_redirect_hop=False,
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=443,
            protocol="",
            resolved_ip="93.184.216.10",
            observed_at=T0,
            is_redirect_hop=False,
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip=" ",
            observed_at=T0,
            is_redirect_hop=False,
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at="not-a-datetime",  # type: ignore[arg-type]
            is_redirect_hop=False,
        )
    naive = datetime(2026, 7, 28, 16, 0)  # no tzinfo
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at=naive,
            is_redirect_hop=False,
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=_grant(),
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at=T0,
            is_redirect_hop="yes",  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        evaluate_egress_request(
            grant=object(),  # type: ignore[arg-type]
            host="app.staging.example.com",
            port=443,
            protocol="https",
            resolved_ip="93.184.216.10",
            observed_at=T0,
            is_redirect_hop=False,
        )


def test_invalid_resolved_ip_is_denied() -> None:
    decision = evaluate_egress_request(
        grant=_grant(),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="not-an-ip",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is False
    assert decision.reason is EgressDenyReason.INVALID_RESOLVED_IP


def test_ipv6_metadata_endpoint_denied() -> None:
    decision = evaluate_egress_request(
        grant=_grant(target=_spec(allow_private_rfc1918=True)),
        host="app.staging.example.com",
        port=443,
        protocol="https",
        resolved_ip="fd00:ec2::254",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is False
    assert decision.reason is EgressDenyReason.FORBIDDEN_METADATA


def test_target_spec_rejects_non_bool_private_flag() -> None:
    with pytest.raises(DomainValidationError):
        _spec(allow_private_rfc1918="yes")  # type: ignore[arg-type]


def test_grant_rejects_invalid_contract_fields() -> None:
    with pytest.raises(DomainValidationError):
        _grant(grant_id=" ")
    with pytest.raises(DomainValidationError):
        _grant(target=object())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        _grant(state="active")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        _grant(max_concurrent_units=0)
    with pytest.raises(DomainValidationError):
        _grant(max_concurrent_units=True)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        _grant(valid_from="now")  # type: ignore[arg-type]
    naive = datetime(2026, 7, 28, 16, 0)
    with pytest.raises(DomainValidationError):
        _grant(valid_from=naive)
    with pytest.raises(DomainValidationError):
        _grant(valid_until=T0)  # equal to valid_from via default? use same as from
    with pytest.raises(DomainValidationError):
        _grant(valid_from=T0, valid_until=T0)
    with pytest.raises(DomainValidationError):
        _grant(version=0)


def test_grant_is_effective_at_rejects_bad_clock() -> None:
    grant = _grant()
    with pytest.raises(DomainValidationError):
        grant.is_effective_at("now")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        grant.is_effective_at(datetime(2026, 7, 28, 16, 0))


def test_blank_dns_pattern_entries_are_ignored_not_matched() -> None:
    # Construction forbids blank names in the tuple; empty-string patterns
    # cannot be stored. Exact host still matches when listed cleanly.
    grant = _grant()
    decision = evaluate_egress_request(
        grant=grant,
        host="app.staging.example.com.",
        port=443,
        protocol="HTTPS",
        resolved_ip="93.184.216.10",
        observed_at=T0 + timedelta(minutes=5),
        is_redirect_hop=False,
    )
    assert decision.allowed is True
    assert decision.host == "app.staging.example.com"
    assert decision.protocol == "https"
