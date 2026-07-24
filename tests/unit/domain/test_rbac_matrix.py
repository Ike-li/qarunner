"""T-M2-RBAC-001: business-role own/non-own authorization matrix.

Observable contract:
- each business role's allow/deny for Suite/Batch actions is stable;
- non-owner access to foreign objects is denied for owner-scoped actions;
- every deny carries an audit-ready decision (decision=deny + reason code);
- admin may act across ownership; auditor is read-only on audit/objects.
"""

from __future__ import annotations


def test_admin_can_create_batch_on_any_project() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    decision = authorize(
        role=BusinessRole.ADMIN,
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.OTHER,
    )
    assert decision.allowed is True
    assert decision.decision == "allow"


def test_owner_can_create_batch_on_own_project_only() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    own = authorize(
        role=BusinessRole.OWNER,
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.OWN,
    )
    other = authorize(
        role=BusinessRole.OWNER,
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.OTHER,
    )
    assert own.allowed is True
    assert other.allowed is False
    assert other.decision == "deny"
    assert other.reason == "not_owner"


def test_maintainer_can_publish_suite_revision_on_own_only() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    own = authorize(
        role=BusinessRole.MAINTAINER,
        action=AuthorizationAction.SUITE_PUBLISH_REVISION,
        ownership=OwnershipScope.OWN,
    )
    other = authorize(
        role=BusinessRole.MAINTAINER,
        action=AuthorizationAction.SUITE_PUBLISH_REVISION,
        ownership=OwnershipScope.OTHER,
    )
    assert own.allowed is True
    assert other.allowed is False
    assert other.reason == "not_owner"


def test_user_cannot_retire_suite() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    decision = authorize(
        role=BusinessRole.USER,
        action=AuthorizationAction.SUITE_RETIRE,
        ownership=OwnershipScope.OWN,
    )
    assert decision.allowed is False
    assert decision.reason == "role_not_permitted"


def test_auditor_can_read_audit_but_not_create_batch() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    audit = authorize(
        role=BusinessRole.AUDITOR,
        action=AuthorizationAction.AUDIT_READ,
        ownership=OwnershipScope.ANY,
    )
    create = authorize(
        role=BusinessRole.AUDITOR,
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.OWN,
    )
    assert audit.allowed is True
    assert create.allowed is False


def test_reviewer_can_adjudicate_but_not_cancel_foreign_batch() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    adjudicate = authorize(
        role=BusinessRole.REVIEWER,
        action=AuthorizationAction.ATTEMPT_ADJUDICATE,
        ownership=OwnershipScope.ANY,
    )
    cancel_other = authorize(
        role=BusinessRole.REVIEWER,
        action=AuthorizationAction.BATCH_CANCEL,
        ownership=OwnershipScope.OTHER,
    )
    assert adjudicate.allowed is True
    assert cancel_other.allowed is False


def test_operator_can_read_workers_and_not_mutate_suite() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    workers = authorize(
        role=BusinessRole.OPERATOR,
        action=AuthorizationAction.WORKER_READ,
        ownership=OwnershipScope.ANY,
    )
    suite = authorize(
        role=BusinessRole.OPERATOR,
        action=AuthorizationAction.SUITE_REGISTER,
        ownership=OwnershipScope.OWN,
    )
    assert workers.allowed is True
    assert suite.allowed is False


def test_deny_decision_is_audit_ready() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    decision = authorize(
        role=BusinessRole.USER,
        action=AuthorizationAction.BATCH_CANCEL,
        ownership=OwnershipScope.OTHER,
    )
    assert decision.allowed is False
    assert decision.decision == "deny"
    assert decision.reason in {"not_owner", "role_not_permitted"}
    assert decision.action is AuthorizationAction.BATCH_CANCEL
    assert decision.role is BusinessRole.USER
    assert decision.ownership is OwnershipScope.OTHER
    # Audit-ready: stable machine codes, no free-form prose required.
    assert decision.reason.replace("_", "").isalnum()


def test_matrix_covers_all_roles_and_actions_without_raising() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    for role in BusinessRole:
        for action in AuthorizationAction:
            for ownership in OwnershipScope:
                decision = authorize(role=role, action=action, ownership=ownership)
                assert decision.decision in {"allow", "deny"}
                assert isinstance(decision.allowed, bool)


def test_invalid_role_type_is_denied() -> None:
    from qarunner.domain import AuthorizationAction, OwnershipScope, authorize

    decision = authorize(
        role="not-a-role",  # type: ignore[arg-type]
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.OWN,
    )
    assert decision.allowed is False
    assert decision.reason == "role_not_permitted"


def test_invalid_action_type_is_denied() -> None:
    from qarunner.domain import BusinessRole, OwnershipScope, authorize

    decision = authorize(
        role=BusinessRole.OWNER,
        action="not-an-action",  # type: ignore[arg-type]
        ownership=OwnershipScope.OWN,
    )
    assert decision.allowed is False
    assert decision.reason == "role_not_permitted"


def test_invalid_ownership_type_defaults_to_other_deny() -> None:
    from qarunner.domain import AuthorizationAction, BusinessRole, authorize

    decision = authorize(
        role=BusinessRole.OWNER,
        action=AuthorizationAction.BATCH_CREATE,
        ownership="weird",  # type: ignore[arg-type]
    )
    assert decision.allowed is False
    assert decision.reason == "not_owner"


def test_owner_any_scope_requires_ownership_proof() -> None:
    from qarunner.domain import (
        AuthorizationAction,
        BusinessRole,
        OwnershipScope,
        authorize,
    )

    decision = authorize(
        role=BusinessRole.OWNER,
        action=AuthorizationAction.BATCH_CREATE,
        ownership=OwnershipScope.ANY,
    )
    assert decision.allowed is False
    assert decision.reason == "not_owner"
