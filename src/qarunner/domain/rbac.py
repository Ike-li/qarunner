"""Business-role authorization matrix (T-M2-RBAC-001).

MVP single-ECS control-plane authorization for Suite/Batch/Audit/Worker actions.
This is the pure policy table; HTTP adapters map callers into BusinessRole +
OwnershipScope and must audit every deny via the returned decision codes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class BusinessRole(enum.StrEnum):
    """Business roles used by M2 object authorization."""

    ADMIN = "admin"
    OWNER = "owner"
    MAINTAINER = "maintainer"
    USER = "user"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class AuthorizationAction(enum.StrEnum):
    """Stable action codes for Suite/Batch/Audit/Worker commands."""

    SUITE_REGISTER = "suite.register"
    SUITE_PUBLISH_REVISION = "suite.publish_revision"
    SUITE_RETIRE = "suite.retire"
    BATCH_CREATE = "batch.create"
    BATCH_CANCEL = "batch.cancel"
    BATCH_READ = "batch.read"
    ATTEMPT_ADJUDICATE = "attempt.adjudicate"
    AUDIT_READ = "audit.read"
    WORKER_READ = "worker.read"
    WORKER_DRAIN = "worker.drain"


class OwnershipScope(enum.StrEnum):
    """Whether the actor owns the target object/project."""

    OWN = "own"
    OTHER = "other"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Audit-ready allow/deny decision for one role/action/ownership triple."""

    role: BusinessRole
    action: AuthorizationAction
    ownership: OwnershipScope
    allowed: bool
    decision: str
    reason: str


# Role → actions that do not require object ownership (global or ANY-scope).
_ROLE_GLOBAL_ACTIONS: dict[BusinessRole, frozenset[AuthorizationAction]] = {
    BusinessRole.ADMIN: frozenset(AuthorizationAction),
    BusinessRole.OPERATOR: frozenset(
        {
            AuthorizationAction.WORKER_READ,
            AuthorizationAction.WORKER_DRAIN,
            AuthorizationAction.BATCH_READ,
            AuthorizationAction.AUDIT_READ,
        }
    ),
    BusinessRole.AUDITOR: frozenset(
        {
            AuthorizationAction.AUDIT_READ,
            AuthorizationAction.BATCH_READ,
            AuthorizationAction.WORKER_READ,
        }
    ),
    BusinessRole.REVIEWER: frozenset(
        {
            AuthorizationAction.ATTEMPT_ADJUDICATE,
            AuthorizationAction.BATCH_READ,
            AuthorizationAction.AUDIT_READ,
        }
    ),
}

# Role → actions allowed only on owned objects/projects.
_ROLE_OWNED_ACTIONS: dict[BusinessRole, frozenset[AuthorizationAction]] = {
    BusinessRole.OWNER: frozenset(
        {
            AuthorizationAction.SUITE_REGISTER,
            AuthorizationAction.SUITE_PUBLISH_REVISION,
            AuthorizationAction.SUITE_RETIRE,
            AuthorizationAction.BATCH_CREATE,
            AuthorizationAction.BATCH_CANCEL,
            AuthorizationAction.BATCH_READ,
            AuthorizationAction.AUDIT_READ,
        }
    ),
    BusinessRole.MAINTAINER: frozenset(
        {
            AuthorizationAction.SUITE_REGISTER,
            AuthorizationAction.SUITE_PUBLISH_REVISION,
            AuthorizationAction.BATCH_CREATE,
            AuthorizationAction.BATCH_READ,
            AuthorizationAction.AUDIT_READ,
        }
    ),
    BusinessRole.USER: frozenset(
        {
            AuthorizationAction.BATCH_CREATE,
            AuthorizationAction.BATCH_CANCEL,
            AuthorizationAction.BATCH_READ,
        }
    ),
}


def authorize(
    *,
    role: BusinessRole,
    action: AuthorizationAction,
    ownership: OwnershipScope,
) -> AuthorizationDecision:
    """Return a stable allow/deny decision for the business RBAC matrix."""
    if not isinstance(role, BusinessRole):
        return AuthorizationDecision(
            role=BusinessRole.USER,
            action=action
            if isinstance(action, AuthorizationAction)
            else AuthorizationAction.BATCH_READ,
            ownership=ownership if isinstance(ownership, OwnershipScope) else OwnershipScope.OTHER,
            allowed=False,
            decision="deny",
            reason="role_not_permitted",
        )
    if not isinstance(action, AuthorizationAction):
        return AuthorizationDecision(
            role=role,
            action=AuthorizationAction.BATCH_READ,
            ownership=ownership if isinstance(ownership, OwnershipScope) else OwnershipScope.OTHER,
            allowed=False,
            decision="deny",
            reason="role_not_permitted",
        )
    if not isinstance(ownership, OwnershipScope):
        ownership = OwnershipScope.OTHER

    if role is BusinessRole.ADMIN:
        return _allow(role=role, action=action, ownership=ownership)

    global_actions = _ROLE_GLOBAL_ACTIONS.get(role, frozenset())
    if action in global_actions:
        return _allow(role=role, action=action, ownership=ownership)

    owned_actions = _ROLE_OWNED_ACTIONS.get(role, frozenset())
    if action not in owned_actions:
        return _deny(
            role=role,
            action=action,
            ownership=ownership,
            reason="role_not_permitted",
        )
    if ownership is OwnershipScope.OTHER:
        return _deny(
            role=role,
            action=action,
            ownership=ownership,
            reason="not_owner",
        )
    # OWN or ANY for owner-scoped actions: ANY is treated as needing ownership proof,
    # so only OWN is allowed here.
    if ownership is OwnershipScope.ANY:
        return _deny(
            role=role,
            action=action,
            ownership=ownership,
            reason="not_owner",
        )
    return _allow(role=role, action=action, ownership=ownership)


def _allow(
    *,
    role: BusinessRole,
    action: AuthorizationAction,
    ownership: OwnershipScope,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        role=role,
        action=action,
        ownership=ownership,
        allowed=True,
        decision="allow",
        reason="permitted",
    )


def _deny(
    *,
    role: BusinessRole,
    action: AuthorizationAction,
    ownership: OwnershipScope,
    reason: str,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        role=role,
        action=action,
        ownership=ownership,
        allowed=False,
        decision="deny",
        reason=reason,
    )
