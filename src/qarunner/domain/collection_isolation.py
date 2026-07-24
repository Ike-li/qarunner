"""Collection isolation boundary (T-M2-COLLECT-001).

Collection is untrusted execution. The control plane may only ingest structured
adapter results via `accept_collection_adapter_result`. It must never import the
suite package tree, open Docker sockets, or hand collection code a live DB/store.
"""

from __future__ import annotations

# Modules/packages the collection accept path and control plane must not expose
# to collection jobs as callable capability.
FORBIDDEN_CONTROL_PLANE_IMPORTS: frozenset[str] = frozenset(
    {
        "qarunner.adapters.docker_runner",
        "qarunner.adapters.postgres_store",
        "qarunner.adapters.sqlite_store",
        "qarunner.adapters.subprocess_runner",
        "qarunner.core.orchestrator",
        "qarunner.api.deps",
        "docker",
        "asyncpg",
        "aiosqlite",
    }
)

# Source tokens that must not appear in the pure accept-path module.
FORBIDDEN_ACCEPT_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "subprocess",
        "importlib",
        "os.system",
        "docker",
        "asyncpg",
        "aiosqlite",
        "__import__",
        "exec(",
        "eval(",
    }
)


def assert_accept_path_is_isolated(*, source: str) -> None:
    """Fail closed when the accept-path source embeds control-plane capability."""
    for token in FORBIDDEN_ACCEPT_PATH_TOKENS:
        if token in source:
            raise AssertionError(f"accept path embeds forbidden capability: {token}")


def is_forbidden_control_plane_import(module_name: str) -> bool:
    """Return True when a module must not be reachable from collection jobs."""
    if module_name in FORBIDDEN_CONTROL_PLANE_IMPORTS:
        return True
    return any(
        module_name == item or module_name.startswith(f"{item}.")
        for item in FORBIDDEN_CONTROL_PLANE_IMPORTS
    )
