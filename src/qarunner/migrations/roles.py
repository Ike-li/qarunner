"""Explicit PostgreSQL service-role bootstrap and least-privilege allowlist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_ROLE_PREFIX_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_LONGEST_ROLE_SUFFIX = "_worker_protocol"

_LEGACY_TABLES = (
    "credentials",
    "run_ai_diagnosis",
    "run_test_cases",
    "runs",
    "schema_migrations",
    "suites",
    "test_profiles",
    "test_schedules",
    "users",
)
# This is an explicit security allowlist snapshot, not a persistence authority. A migration that
# adds a relation must update this list before role grants can be applied; unknown/missing catalog
# entries fail closed in ``apply_database_role_grants``.
_GREENFIELD_TABLES = (
    "qep_assignments",
    "qep_attempt_events",
    "qep_attempts",
    "qep_audit_events",
    "qep_batch_cancellation_intents",
    "qep_batch_cancellation_scope_items",
    "qep_batch_finalization_bases",
    "qep_batch_finalization_readiness_facts",
    "qep_batch_item_resolutions",
    "qep_batch_preexecution_closure_bases",
    "qep_batch_preexecution_scope_items",
    "qep_batch_rejections",
    "qep_batch_success_policies",
    "qep_batches",
    "qep_case_manifests",
    "qep_duplicate_risk_acceptances",
    "qep_evidence_index",
    "qep_manifest_items",
    "qep_materialized_scope_handoffs",
    "qep_outbox_events",
    "qep_platform_retry_policies",
    "qep_preexecution_tasks",
    "qep_principals",
    "qep_projects",
    "qep_resource_profiles",
    "qep_retry_intents",
    "qep_role_bindings",
    "qep_run_finalization_bases",
    "qep_run_item_resolutions",
    "qep_run_manifest_items",
    "qep_runs",
    "qep_shard_plans",
    "qep_suite_retry_policies",
    "qep_suite_revisions",
    "qep_suites",
    "qep_unknown_adjudications",
    "qep_unknown_observations",
    "qep_unknown_review_policies",
    "qep_versioned_fact_commands",
    "qep_versioned_fact_snapshots",
    "qep_worker_generations",
    "qep_workers",
)
CURRENT_TABLES = tuple(
    sorted(
        {
            "alembic_version",
            *_LEGACY_TABLES,
            *_GREENFIELD_TABLES,
        }
    )
)
CURRENT_SEQUENCES = ("run_test_cases_id_seq",)

_API_TABLE_PRIVILEGES = {
    "alembic_version": ("SELECT",),
    "credentials": ("DELETE", "INSERT", "SELECT"),
    "run_ai_diagnosis": ("INSERT", "SELECT", "UPDATE"),
    "run_test_cases": ("DELETE", "INSERT", "SELECT"),
    "runs": ("DELETE", "INSERT", "SELECT", "UPDATE"),
    "suites": ("DELETE", "INSERT", "SELECT", "UPDATE"),
    "test_profiles": ("DELETE", "INSERT", "SELECT", "UPDATE"),
    "test_schedules": ("DELETE", "INSERT", "SELECT", "UPDATE"),
    "users": ("DELETE", "INSERT", "SELECT", "UPDATE"),
}
_COORDINATOR_SELECT_TABLES = {
    "alembic_version",
    "qep_assignments",
    "qep_attempts",
    "qep_audit_events",
    "qep_batch_cancellation_intents",
    "qep_batch_cancellation_scope_items",
    "qep_batch_finalization_bases",
    "qep_batch_finalization_readiness_facts",
    "qep_batch_item_resolutions",
    "qep_batch_preexecution_closure_bases",
    "qep_batch_rejections",
    "qep_batch_success_policies",
    "qep_batches",
    "qep_case_manifests",
    "qep_evidence_index",
    "qep_manifest_items",
    "qep_materialized_scope_handoffs",
    "qep_outbox_events",
    "qep_retry_intents",
    "qep_run_finalization_bases",
    "qep_run_item_resolutions",
    "qep_runs",
    "qep_shard_plans",
    "qep_suite_revisions",
    "qep_suites",
    "qep_versioned_fact_commands",
    "qep_versioned_fact_snapshots",
}
_COORDINATOR_INSERT_TABLES = {
    "qep_audit_events",
    "qep_batch_cancellation_intents",
    "qep_batch_finalization_bases",
    "qep_batch_finalization_readiness_facts",
    "qep_batch_item_resolutions",
    "qep_evidence_index",
    "qep_materialized_scope_handoffs",
    "qep_outbox_events",
    "qep_run_finalization_bases",
    "qep_run_item_resolutions",
    "qep_versioned_fact_commands",
    "qep_versioned_fact_snapshots",
}
_COORDINATOR_UPDATE_TABLES = {
    "qep_assignments",
    "qep_attempts",
    "qep_batches",
    "qep_runs",
}
_RECOVERY_EXCLUDED_TABLES = {"credentials", "users"}


class RoleBootstrapError(RuntimeError):
    """Raised before applying an incomplete or unsafe database-role configuration."""


@dataclass(frozen=True)
class DatabaseRoleNames:
    """Names for the M1 database identities derived from one safe prefix."""

    migrator: str
    api: str
    coordinator: str
    worker_protocol: str
    recovery_read: str

    @classmethod
    def from_prefix(cls, prefix: str) -> DatabaseRoleNames:
        safe_prefix = validate_role_prefix(prefix)
        return cls(
            migrator=f"{safe_prefix}_migrator",
            api=f"{safe_prefix}_api",
            coordinator=f"{safe_prefix}_coordinator",
            worker_protocol=f"{safe_prefix}_worker_protocol",
            recovery_read=f"{safe_prefix}_recovery_read",
        )

    @property
    def all(self) -> tuple[str, ...]:
        return (
            self.migrator,
            self.api,
            self.coordinator,
            self.worker_protocol,
            self.recovery_read,
        )

    @property
    def runtime(self) -> tuple[str, ...]:
        return (self.api, self.coordinator, self.worker_protocol, self.recovery_read)


def validate_role_prefix(prefix: str) -> str:
    """Accept only prefixes whose generated PostgreSQL role names remain unambiguous."""
    if (
        _ROLE_PREFIX_PATTERN.fullmatch(prefix) is None
        or len(prefix) + len(_LONGEST_ROLE_SUFFIX) > 63
    ):
        raise ValueError("PostgreSQL role prefix must be a safe lowercase identifier")
    return prefix


def database_role_matrix(
    roles: DatabaseRoleNames,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return the complete table/action allowlist; absent entries are denied."""
    coordinator: dict[str, tuple[str, ...]] = {}
    for table in _COORDINATOR_SELECT_TABLES:
        privileges = {"SELECT"}
        if table in _COORDINATOR_INSERT_TABLES:
            privileges.add("INSERT")
        if table in _COORDINATOR_UPDATE_TABLES:
            privileges.add("UPDATE")
        coordinator[table] = tuple(sorted(privileges))

    recovery = {
        table: ("SELECT",) for table in CURRENT_TABLES if table not in _RECOVERY_EXCLUDED_TABLES
    }
    return {
        roles.migrator: {},
        roles.api: dict(_API_TABLE_PRIVILEGES),
        roles.coordinator: coordinator,
        roles.worker_protocol: {},
        roles.recovery_read: recovery,
    }


async def bootstrap_database_roles(
    connection: Any,
    *,
    schema: str,
    roles: DatabaseRoleNames,
) -> None:
    """Create restricted login roles and one migrator-owned disposable/application Schema."""
    safe_schema = _safe_schema_name(schema)
    if safe_schema == "public":
        raise RoleBootstrapError("role bootstrap refuses the shared public Schema")

    for role in roles.all:
        await _ensure_restricted_login_role(connection, role)

    owner = await connection.fetchval(
        """
        SELECT owner.rolname
        FROM pg_catalog.pg_namespace AS namespace
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = namespace.nspowner
        WHERE namespace.nspname = $1
        """,
        safe_schema,
    )
    if owner is None:
        await connection.execute(
            f"CREATE SCHEMA {_quote_identifier(safe_schema)} "
            f"AUTHORIZATION {_quote_identifier(roles.migrator)}"
        )
    elif owner != roles.migrator:
        raise RoleBootstrapError(f"Schema {safe_schema!r} must be owned by the migrator role")

    database = await connection.fetchval("SELECT current_database()")
    for role in roles.all:
        await connection.execute(
            f"GRANT CONNECT ON DATABASE {_quote_identifier(database)} TO {_quote_identifier(role)}"
        )


async def apply_database_role_grants(
    connection: Any,
    *,
    schema: str,
    roles: DatabaseRoleNames,
) -> None:
    """Reconcile the explicit role allowlist on an exact Alembic-head catalog."""
    safe_schema = _safe_schema_name(schema)
    if safe_schema == "public":
        raise RoleBootstrapError("role grants refuse the shared public Schema")

    owner = await connection.fetchval(
        """
        SELECT owner.rolname
        FROM pg_catalog.pg_namespace AS namespace
        JOIN pg_catalog.pg_roles AS owner ON owner.oid = namespace.nspowner
        WHERE namespace.nspname = $1
        """,
        safe_schema,
    )
    if owner != roles.migrator:
        raise RoleBootstrapError(f"Schema {safe_schema!r} must be owned by the migrator role")
    if await connection.fetchval("SELECT session_user") != roles.migrator:
        raise RoleBootstrapError("role grants must run as the migrator role")
    if not await connection.fetchval(
        "SELECT pg_has_role(current_user, $1, 'USAGE')",
        roles.migrator,
    ):
        raise RoleBootstrapError("role grants must run as the migrator role")

    await _validate_schema_revision(connection, schema=safe_schema)
    actual_tables = {
        row["table_name"]
        for row in await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            """,
            safe_schema,
        )
    }
    expected_tables = set(CURRENT_TABLES)
    unknown_tables = sorted(actual_tables - expected_tables)
    missing_tables = sorted(expected_tables - actual_tables)
    if unknown_tables:
        raise RoleBootstrapError(f"unreviewed tables in role catalog: {unknown_tables}")
    if missing_tables:
        raise RoleBootstrapError(f"missing tables in role catalog: {missing_tables}")

    actual_sequences = {
        row["sequence_name"]
        for row in await connection.fetch(
            """
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = $1
            """,
            safe_schema,
        )
    }
    if actual_sequences != set(CURRENT_SEQUENCES):
        raise RoleBootstrapError(
            "sequence catalog does not match the reviewed role matrix: "
            f"actual={sorted(actual_sequences)}, expected={sorted(CURRENT_SEQUENCES)}"
        )

    quoted_schema = _quote_identifier(safe_schema)
    quoted_runtime_roles = ", ".join(_quote_identifier(role) for role in roles.runtime)
    await connection.execute(f"REVOKE ALL ON SCHEMA {quoted_schema} FROM PUBLIC")
    await connection.execute(f"REVOKE ALL ON SCHEMA {quoted_schema} FROM {quoted_runtime_roles}")
    await connection.execute(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_runtime_roles}")
    await connection.execute(
        f"GRANT CREATE, USAGE ON SCHEMA {quoted_schema} TO {_quote_identifier(roles.migrator)}"
    )

    matrix = database_role_matrix(roles)
    for table in CURRENT_TABLES:
        qualified_table = f"{quoted_schema}.{_quote_identifier(table)}"
        await connection.execute(f"REVOKE ALL ON TABLE {qualified_table} FROM PUBLIC")
        await connection.execute(
            f"REVOKE ALL ON TABLE {qualified_table} FROM {quoted_runtime_roles}"
        )
    for role, table_grants in matrix.items():
        for table, privileges in table_grants.items():
            qualified_table = f"{quoted_schema}.{_quote_identifier(table)}"
            await connection.execute(
                f"GRANT {', '.join(privileges)} ON TABLE {qualified_table} "
                f"TO {_quote_identifier(role)}"
            )

    for sequence in CURRENT_SEQUENCES:
        qualified_sequence = f"{quoted_schema}.{_quote_identifier(sequence)}"
        await connection.execute(f"REVOKE ALL ON SEQUENCE {qualified_sequence} FROM PUBLIC")
        await connection.execute(
            f"REVOKE ALL ON SEQUENCE {qualified_sequence} FROM {quoted_runtime_roles}"
        )
    await connection.execute(
        f"GRANT USAGE ON SEQUENCE {quoted_schema}."
        f"{_quote_identifier(CURRENT_SEQUENCES[0])} TO {_quote_identifier(roles.api)}"
    )

    quoted_migrator = _quote_identifier(roles.migrator)
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_migrator} IN SCHEMA {quoted_schema} "
        "REVOKE ALL ON TABLES FROM PUBLIC"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_migrator} IN SCHEMA {quoted_schema} "
        f"REVOKE ALL ON TABLES FROM {quoted_runtime_roles}"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_migrator} IN SCHEMA {quoted_schema} "
        "REVOKE ALL ON SEQUENCES FROM PUBLIC"
    )
    await connection.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_migrator} IN SCHEMA {quoted_schema} "
        f"REVOKE ALL ON SEQUENCES FROM {quoted_runtime_roles}"
    )


async def _ensure_restricted_login_role(
    connection: Any,
    role: str,
) -> None:
    row = await connection.fetchrow(
        """
        SELECT role.rolcanlogin, role.rolinherit, role.rolsuper, role.rolcreatedb,
               role.rolcreaterole, role.rolreplication, role.rolbypassrls,
               EXISTS (
                   SELECT 1
                   FROM pg_catalog.pg_auth_members AS membership
                   WHERE membership.member = role.oid
               ) AS has_memberships
        FROM pg_catalog.pg_roles AS role
        WHERE role.rolname = $1
        """,
        role,
    )
    if row is None:
        await connection.execute(
            f"CREATE ROLE {_quote_identifier(role)} LOGIN "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        )
        return
    if (
        not row["rolcanlogin"]
        or row["rolinherit"]
        or row["has_memberships"]
        or any(
            row[key]
            for key in (
                "rolsuper",
                "rolcreatedb",
                "rolcreaterole",
                "rolreplication",
                "rolbypassrls",
            )
        )
    ):
        raise RoleBootstrapError(f"existing role {role!r} is not a restricted login role")


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def _validate_schema_revision(connection: Any, *, schema: str) -> None:
    """Load runtime validation lazily so role inspection has no startup side effects."""
    from qarunner.migrations.runtime import validate_schema_revision

    await validate_schema_revision(connection, schema=schema)


def _safe_schema_name(schema: str) -> str:
    """Load the shared identifier validator only when an operator action actually runs."""
    from qarunner.migrations.config import validate_schema_name

    return validate_schema_name(schema)
