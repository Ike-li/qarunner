"""FastAPI routes — thin HTTP layer over the orchestrator / store / auth."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import mimetypes
import os
import re
import stat
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from qarunner.api.deps import Container, get_current_admin, get_current_user
from qarunner.api.schemas import (
    CaseHistoryResponse,
    CloneTestSuiteRequest,
    CredentialCreateRequest,
    CredentialListResponse,
    CredentialResponse,
    LinkTestSuiteRequest,
    LinkTestSuiteResponse,
    LockRunRequest,
    LoginRequest,
    RunArtifactListResponse,
    RunArtifactResponse,
    RunDiffBaselineInfo,
    RunDiffResponse,
    RunListResponse,
    RunResponse,
    RunTrendResponse,
    SuiteInfoResponse,
    TestProfileCreateRequest,
    TestProfileResponse,
    TestProfileUpdateRequest,
    TestScheduleCreateRequest,
    TestSchedulePreviewResponse,
    TestScheduleResponse,
    TestScheduleUpdateRequest,
    TokenResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
    profile_to_response,
    run_to_response,
    schedule_to_response,
)
from qarunner.core import flaky, regression, trend
from qarunner.core.auth import create_access_token, hash_password, verify_password
from qarunner.core.credentials import CredentialCipher
from qarunner.errors import (
    InvalidScheduleRequest,
    LoginLockedOut,
    ProfileNotFound,
    RunNotFound,
    ScheduleNotFound,
    UnknownRunner,
    UnsafePath,
)
from qarunner.models import (
    Credential,
    MetricsSummary,
    ReportRef,
    Run,
    RunRequest,
    RunStatus,
    SuiteMetrics,
    TestSuite,
    User,
    UserRole,
)
from qarunner.ports.store import Store

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_owner_access(created_by: str, user: User) -> None:
    """Raise 403 unless *user* owns the resource (by ``created_by``) or is admin.

    Object-level authorization shared by runs, profiles and schedules: each of
    those records its creator and a non-admin may only touch its own. SEC-4
    originally hardened runs only; profiles and schedules were an IDOR gap —
    any logged-in user could list/read/modify/delete another user's.
    """
    if user.role != UserRole.ADMIN and created_by != user.username:
        raise HTTPException(status_code=403, detail="Access denied")


def _require_run_access(run: Run, user: User) -> None:
    """Raise 403 unless *user* owns *run* or is an admin (object-level authz)."""
    _require_owner_access(run.created_by, user)


async def _require_profile_access(container: Container, profile_id: str, user: User) -> None:
    """Raise 403 when an existing profile is not usable by *user*.

    Missing profiles are left to the profile/schedule service so existing 400/404
    error mapping stays unchanged.
    """
    profile = await container.store.get_profile(profile_id)
    if profile is not None:
        _require_owner_access(profile.created_by, user)


def _client_ip(request: Request) -> str:
    """Best-effort client IP for audit/throttle keys ('unknown' if unavailable).

    Returns the transport peer address; behind a reverse proxy this is the
    proxy's IP, so a trusted-proxy ``X-Forwarded-For`` story is needed before
    relying on it for per-client throttling in such deployments (SEC-6 /
    deployment hardening).
    """
    return request.client.host if request.client else "unknown"


# ── External test-suite git operations (stage 2) ─────────────────────────

# Bounds clone/fetch so a hung or hostile remote can't pin a worker forever.
_GIT_TIMEOUT = 300.0
# npm ci pulls a full dependency tree and can be slow; allow longer than git.
_NPM_TIMEOUT = 600.0


async def _run_cmd(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* with a parametrised argv (no shell) under a timeout.

    Returns ``(returncode, stdout, stderr)``. The argv form (never a shell
    string) keeps URLs / refs / paths from being interpreted as commands. On
    timeout the process is killed and a non-zero code is synthesised so callers
    handle it exactly like any other failure. ``env`` (when given) replaces the
    child environment — used to inject git credentials off the argv (P0-1).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"{cmd[0]} operation timed out"
    return proc.returncode, out_b.decode(errors="replace"), err_b.decode(errors="replace")


async def _run_git(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float = _GIT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ``git <args>`` via :func:`_run_cmd` (parametrised argv + timeout)."""
    return await _run_cmd(["git", *args], cwd=cwd, timeout=timeout, env=env)


# A throwaway script git invokes when it needs a username/password. It carries
# NO secret itself — the token rides ``QARUNNER_GIT_PASS`` in the child env, so it
# never lands in argv, the URL, or on disk in plaintext. Username is a constant
# accepted by GitHub/GitLab for PAT auth.
_GIT_ASKPASS_SCRIPT = (
    "#!/bin/sh\n"
    'case "$1" in\n'
    'Username*) printf "%s" "$QARUNNER_GIT_USER" ;;\n'
    '*) printf "%s" "$QARUNNER_GIT_PASS" ;;\n'
    "esac\n"
)


@contextlib.asynccontextmanager
async def _git_auth_env(secret: str | None) -> AsyncIterator[dict[str, str] | None]:
    """Yield a child env that injects an HTTPS token via ``GIT_ASKPASS`` (P0-1).

    Returns ``None`` (inherit the parent env) when there's no credential. The
    askpass script is written to a private temp file, made executable, and
    removed on exit; the token is passed only through the environment.
    """
    if secret is None:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="qa-askpass-", suffix=".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_GIT_ASKPASS_SCRIPT)
        os.chmod(path, stat.S_IRWXU)  # 0o700 — owner-only
        # Whitelist: only pass env vars git actually needs (PATH, HOME, USER
        # for ssh/config resolution, locale vars).  Do NOT pass the full host
        # environment — that leaks SSH_AUTH_SOCK, BASH_ENV, LD_*, etc. into
        # the git child process.  Pattern matches subprocess_runner._ENV_ALLOWLIST.
        _GIT_ENV_WHITELIST = frozenset(
            {
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "LANG",
                "LANGUAGE",
                "LC_ALL",
                "LC_CTYPE",
                "TMPDIR",
                "TEMP",
                "TMP",
                "TZ",
            }
        )
        yield {
            **{k: v for k, v in os.environ.items() if k in _GIT_ENV_WHITELIST},
            "GIT_ASKPASS": path,
            "GIT_TERMINAL_PROMPT": "0",
            "QARUNNER_GIT_USER": "x-access-token",
            "QARUNNER_GIT_PASS": secret,
        }
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


async def _resolve_credential_secret(
    store: Store, credential_ref: str | None, current_user: User, secret_key: str
) -> str | None:
    """Resolve a credential_ref to a decrypted secret for git auth (P0-1).

    Owner-scoped: a non-admin can only use their own credentials. Returns None
    when no ref was given; raises 400 for an unknown ref, 403 for someone else's.
    """
    if not credential_ref:
        return None
    cred = await store.get_credential(credential_ref)
    if cred is None:
        raise HTTPException(status_code=400, detail=f"Credential '{credential_ref}' not found")
    _require_owner_access(cred.created_by, current_user)
    enc = await store.get_credential_secret(credential_ref)
    if enc is None:
        # Raced with a concurrent delete after the existence check — degrade to
        # no auth (like pull) instead of crashing on decrypt(None).
        return None
    return CredentialCipher(secret_key).decrypt(enc)


def _scrub_paths(text: str, *paths: str) -> str:
    """Strip absolute server paths from subprocess output before it reaches an
    API error detail. The suites root / suite path are platform-internal and
    must not leak to clients (same posture as the run-error path-leak fix)."""
    for p in paths:
        text = text.replace(p, "<suite>")
    return text


def _validate_git_url(url: str) -> None:
    """Reject repo URLs outside the allowlist (SSRF / local-file / command exec).

    Only ``https://`` and scp-style ``git@`` are accepted; ``file://``, ``ext::``,
    plain ``http://`` and anything else are refused with 400. Credentials must
    use ``credential_ref`` so tokens never land in the clone URL or git argv.
    """
    if url.startswith("https://"):
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise HTTPException(
                status_code=400,
                detail="Repository URL must not include credentials; use credential_ref.",
            )
        return
    if url.startswith("git@"):
        return
    raise HTTPException(
        status_code=400,
        detail="Unsupported repository URL: only https:// and git@ are allowed.",
    )


def _suite_name_from_url(url: str) -> str:
    """Derive a suite directory name from a clone URL (last segment, no ``.git``)."""
    tail = url.rstrip("/").replace(":", "/").rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail


def _safe_suite_path(tests_root: Path, name: str) -> Path:
    """Resolve ``tests_root/name``, allowing only a single safe path component.

    Blocks traversal: a name containing a separator, a leading dot, or ``.``/
    ``..`` is refused with 400 so clone/pull/delete can only ever touch a direct
    child of the suites root.
    """
    if (not name) or ("/" in name) or ("\\" in name) or name.startswith("."):
        raise HTTPException(status_code=400, detail=f"Invalid suite name: {name!r}")
    return tests_root / name


def _scan_suite_dirs(tests_root: Path) -> list[str]:
    """Return sorted suite directory names directly under *tests_root*.

    Skips hidden (``.``) and dunder (``__``) entries; the filesystem is the
    source of truth for which suites exist (R5).
    """
    names: list[str] = []
    for entry in tests_root.iterdir():
        if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("__"):
            names.append(entry.name)
    names.sort()
    return names


# ── Health ───────────────────────────────────────────────────────────────


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    """Unauthenticated liveness + readiness probe (DEP-4).

    Returns 200 only when the process can reach its database; a failing DB
    round-trip yields 503 so an orchestrator stops routing traffic to a broken
    instance.
    """
    container = request.app.state.container
    try:
        await container.store.get_user("__health_probe__")
    except Exception as exc:
        logger.warning("Health probe failed: database unreachable", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ok"}


# ── Auth & User Management Endpoints ─────────────────────────────────────


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> TokenResponse:
    """Authenticate credentials and return a JWT access token.

    Brute-force protection (SEC-5): repeated failures for a (username, client IP)
    pair trigger an exponential-backoff lockout answered with HTTP 429; failures
    are audited (never the password).

    The token is also planted as an HttpOnly, SameSite=Strict cookie (SEC-6) so
    the browser carries it automatically for same-origin report/stream/download
    requests — no token need ever appear in a URL — and page JS can never read
    it (XSS). The body still returns the token for programmatic API clients.
    """
    container = request.app.state.container
    client_ip = _client_ip(request)
    throttle_key = f"{req.username}|{client_ip}"
    try:
        container.login_throttle.check(throttle_key)
    except LoginLockedOut as exc:
        logger.warning("Locked-out login attempt username=%r ip=%s", req.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    user_record = await container.store.get_user(req.username)
    if not user_record or not verify_password(req.password, user_record["password_hash"]):
        container.login_throttle.record_failure(throttle_key)
        logger.warning("Failed login username=%r ip=%s", req.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    container.login_throttle.record_success(throttle_key)
    token = create_access_token(user_record["username"], user_record["role"], container.settings)
    response.set_cookie(
        "token",
        token,
        max_age=container.settings.access_token_expire_minutes * 60,
        httponly=True,
        samesite="strict",
        secure=container.settings.cookie_secure,
        path="/",
    )
    return TokenResponse(access_token=token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    """Clear the auth cookie (SEC-6).

    Deliberately unauthenticated: an expired or otherwise-invalid session must
    still be tearable down, and clearing a cookie leaks nothing. The delete must
    echo the same path/SameSite/Secure attributes used at login or the browser
    keeps the original cookie.
    """
    settings = request.app.state.container.settings
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(
        "token",
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return resp


@router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Retrieve details of the currently authenticated user."""
    return UserResponse(
        username=current_user.username,
        role=current_user.role,
        created_at=current_user.created_at.isoformat(),
    )


@router.post("/users", status_code=201, response_model=UserResponse)
async def create_user(
    req: UserCreateRequest,
    request: Request,
    _admin_user: User = Depends(get_current_admin),
) -> UserResponse:
    """Create a new user (Admin-only)."""
    container = request.app.state.container
    existing = await container.store.get_user(req.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    hashed = hash_password(req.password)
    await container.store.create_user(req.username, hashed, req.role.value)

    # Fetch newly created user record to construct response. A missing record
    # here means the store violated its create→read invariant; surface it as a
    # 500 with an explicit check (an `assert` would be stripped under `python -O`).
    new_user = await container.store.get_user(req.username)
    if new_user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User was created but could not be retrieved",
        )
    return UserResponse(
        username=new_user["username"],
        role=new_user["role"],
        created_at=new_user["created_at"],
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    _admin_user: User = Depends(get_current_admin),
) -> UserListResponse:
    """List all users in the system (Admin-only)."""
    container = request.app.state.container
    users_records = await container.store.list_users()
    users = [
        UserResponse(
            username=u["username"],
            role=u["role"],
            created_at=u["created_at"],
        )
        for u in users_records
    ]
    return UserListResponse(users=users)


@router.delete("/users/{username}")
async def delete_user(
    username: str,
    request: Request,
    admin_user: User = Depends(get_current_admin),
) -> dict:
    """Delete a user (Admin-only). An admin can't delete their own account —
    locking yourself out is never the intent and a self-delete mid-session would
    invalidate the live token."""
    container = request.app.state.container
    if username == admin_user.username:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    existing = await container.store.get_user(username)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    await container.store.delete_user(username)
    return {"status": "success", "message": f"User {username} deleted"}


@router.put("/users/{username}", response_model=UserResponse)
async def update_user(
    username: str,
    req: UserUpdateRequest,
    request: Request,
    admin_user: User = Depends(get_current_admin),
) -> UserResponse:
    """Update a user's password and/or role (Admin-only).

    The last remaining admin can't be demoted, or the platform would lock itself
    out of every admin-only operation (covers demoting yourself when you're the
    sole admin, as well as demoting another sole admin).
    """
    container = request.app.state.container
    if req.password is None and req.role is None:
        raise HTTPException(
            status_code=400, detail="Nothing to update: provide password and/or role."
        )
    existing = await container.store.get_user(username)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")

    if req.role is not None and existing["role"] == "admin" and req.role.value != "admin":
        if username == admin_user.username:
            raise HTTPException(status_code=400, detail="You cannot demote your own account.")
        users = await container.store.list_users()
        admin_count = sum(1 for u in users if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last remaining admin.")

    if req.password is not None:
        await container.store.update_password(username, hash_password(req.password))
    if req.role is not None:
        await container.store.update_role(username, req.role.value)

    new_role = req.role.value if req.role is not None else existing["role"]
    return UserResponse(
        username=existing["username"],
        role=new_role,
        created_at=existing["created_at"],
    )


# ── Git Credentials (P0-1) ───────────────────────────────────────────────


def _credential_to_response(cred: Credential) -> CredentialResponse:
    return CredentialResponse(
        id=cred.id,
        name=cred.name,
        type=cred.type,
        created_by=cred.created_by,
        created_at=cred.created_at.isoformat(),
    )


@router.post("/credentials", status_code=201, response_model=CredentialResponse)
async def create_credential(
    req: CredentialCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> CredentialResponse:
    """Store a git credential. The secret is encrypted at rest (Fernet, key
    derived from SECRET_KEY) and never returned — only injected into git auth."""
    container = request.app.state.container
    cipher = CredentialCipher(container.settings.secret_key)
    cred = Credential(
        id=uuid.uuid4().hex,
        name=req.name,
        type=req.type,
        created_by=current_user.username,
        created_at=datetime.now(UTC),
    )
    await container.store.save_credential(cred, cipher.encrypt(req.secret))
    return _credential_to_response(cred)


@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> CredentialListResponse:
    """List credential metadata (no secrets). Non-admins see only their own."""
    container = request.app.state.container
    creds = await container.store.list_credentials()
    visible = [
        c
        for c in creds
        if current_user.role == UserRole.ADMIN or c.created_by == current_user.username
    ]
    return CredentialListResponse(credentials=[_credential_to_response(c) for c in visible])


@router.delete("/credentials/{credential_id}")
async def delete_credential(
    credential_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a credential (owner/admin)."""
    container = request.app.state.container
    existing = await container.store.get_credential(credential_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Credential {credential_id} not found")
    _require_owner_access(existing.created_by, current_user)
    await container.store.delete_credential(credential_id)
    return {"status": "success", "message": f"Credential {credential_id} deleted"}


# ── Run Orchestration Endpoints (Secured with JWT) ───────────────────────


@router.get("/tests", response_model=list[str])
async def list_tests(
    request: Request, _current_user: User = Depends(get_current_user)
) -> list[str]:
    """List all available test directories directly under tests_root."""
    cfg = request.app.state.container.settings
    tests_root = Path(cfg.tests_root).resolve()
    if not tests_root.is_dir():
        return []
    return await asyncio.to_thread(_scan_suite_dirs, tests_root)


@router.get("/suites", response_model=list[SuiteInfoResponse])
async def list_suites_detailed(
    request: Request, _current_user: User = Depends(get_current_user)
) -> list[SuiteInfoResponse]:
    """List suites as filesystem entities left-joined with their metadata (R5).

    Each directory under tests_root is returned with its recorded source / repo
    / ref; directories with no record default to ``local`` so manually placed
    suites still appear. Drives the frontend's source-aware actions (stage 5).
    """
    container = request.app.state.container
    cfg = container.settings
    store = container.store
    tests_root = Path(cfg.tests_root).resolve()
    if not tests_root.is_dir():
        return []

    names = await asyncio.to_thread(_scan_suite_dirs, tests_root)
    suites: list[SuiteInfoResponse] = []
    for name in names:
        record = await store.get_suite(name)
        if record is not None:
            suites.append(
                SuiteInfoResponse(
                    name=name,
                    source=record.source,
                    repo_url=record.repo_url,
                    ref=record.ref,
                )
            )
        else:
            suites.append(SuiteInfoResponse(name=name))
    return suites


@router.post("/tests/link", response_model=LinkTestSuiteResponse)
async def link_test_suite(
    request: Request,
    payload: LinkTestSuiteRequest,
    current_user: User = Depends(get_current_user),
) -> LinkTestSuiteResponse:
    """Create a symlink under tests_root pointing to the specified local directory path."""
    import os
    import shutil

    container = request.app.state.container
    cfg = container.settings
    tests_root = Path(cfg.tests_root).resolve()

    if not tests_root.is_dir():
        try:
            tests_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            msg = _scrub_paths(str(e), str(tests_root))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create tests_root directory: {msg}",
            ) from e

    target_path = Path(payload.path)
    suite_name = target_path.name
    if not suite_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: unable to extract directory name.",
        )

    link_path = tests_root / suite_name

    # Object-level authz BEFORE any destructive overwrite: a recorded suite may
    # only be replaced by its owner or an admin; an unregistered directory/symlink
    # already on disk (manually placed) is admin-only. Mirrors delete_test_suite
    # so /link can't bypass /delete's guard (rmtree + owner-hijack via save_suite).
    existing_suite = await container.store.get_suite(suite_name)
    if existing_suite is not None:
        _require_owner_access(existing_suite.created_by, current_user)
    elif (link_path.exists() or link_path.is_symlink()) and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    # Clear existing if it exists
    if link_path.exists() or link_path.is_symlink():
        try:
            if link_path.is_symlink() or link_path.is_file():
                link_path.unlink()
            else:
                shutil.rmtree(link_path)
        except Exception as e:
            msg = _scrub_paths(str(e), str(link_path), str(tests_root))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to clear existing test suite entry: {msg}",
            ) from e

    try:
        os.symlink(payload.path, link_path)
    except Exception as e:
        msg = _scrub_paths(str(e), str(link_path), str(tests_root))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create symlink: {msg}",
        ) from e

    # Verify if target path is resolved and is an accessible directory
    is_accessible = link_path.is_dir()

    if is_accessible:
        message = f"Successfully linked '{suite_name}'!"
    else:
        message = (
            f"Symlink created under external_tests, but the target path '{payload.path}' "
            "is not accessible in this container environment. "
            "Please make sure you have mapped this volume inside docker-compose.yml."
        )

    # Register the suite with metadata + ownership so it can later be updated,
    # deleted or authorised against (stage 1 store; ``local`` source).
    await container.store.save_suite(
        TestSuite(
            name=suite_name,
            source="local",
            repo_url=None,
            ref=None,
            credential_ref=None,
            created_by=current_user.username,
            created_at=datetime.now(UTC),
        )
    )

    return LinkTestSuiteResponse(
        success=True,
        suite_name=suite_name,
        is_accessible=is_accessible,
        message=message,
    )


@router.post("/tests/clone", response_model=LinkTestSuiteResponse)
async def clone_test_suite(
    request: Request,
    payload: CloneTestSuiteRequest,
    current_user: User = Depends(get_current_user),
) -> LinkTestSuiteResponse:
    """Clone a git repository into tests_root as a new ``git`` suite.

    Any logged-in user may clone; ``pull``/``delete`` are owner-scoped. The URL
    is allowlisted (https / ssh only), the name is traversal-checked, and git
    runs with a parametrised argv under a timeout. The resolved ref is recorded
    so a later ``/pull`` can fetch a concrete branch (R6).
    """
    container = request.app.state.container
    store = container.store
    cfg = container.settings

    _validate_git_url(payload.url)
    tests_root = Path(cfg.tests_root).resolve()
    tests_root.mkdir(parents=True, exist_ok=True)

    name = payload.name or _suite_name_from_url(payload.url)
    suite_path = _safe_suite_path(tests_root, name)

    if suite_path.exists() or await store.get_suite(name) is not None:
        raise HTTPException(status_code=409, detail=f"Suite '{name}' already exists")

    # Resolve auth before touching the network so an unknown/forbidden
    # credential_ref fails fast without spawning git.
    secret = await _resolve_credential_secret(
        store, payload.credential_ref, current_user, cfg.secret_key
    )
    args = ["clone", "--depth", "1"]
    if payload.ref:
        args += ["-b", payload.ref]
    args += ["--", payload.url, str(suite_path)]
    async with _git_auth_env(secret) as env:
        rc, _out, err = await _run_git(args, env=env)
    if rc != 0:
        msg = _scrub_paths(err.strip(), str(suite_path), str(tests_root))
        raise HTTPException(status_code=502, detail=f"git clone failed: {msg}")

    # Record the concrete ref. An explicit ref is trusted as given (a tag would
    # leave HEAD detached, so rev-parse is unreliable — N1); otherwise resolve
    # the checked-out default branch (R6).
    if payload.ref:
        ref: str | None = payload.ref
    else:
        rc2, out2, _err2 = await _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(suite_path)
        )
        ref = out2.strip() if (rc2 == 0 and out2.strip()) else None

    try:
        await store.save_suite(
            TestSuite(
                name=name,
                source="git",
                repo_url=payload.url,
                ref=ref,
                credential_ref=payload.credential_ref,
                created_by=current_user.username,
                created_at=datetime.now(UTC),
            )
        )
    except Exception:
        # The working tree is on disk but persistence failed. Roll it back so a
        # later /pull or /delete can't trip over an orphan the store can't see
        # (P1-5). rmtree is best-effort — a cleanup failure must not mask the 503.
        import shutil

        await asyncio.to_thread(shutil.rmtree, suite_path, ignore_errors=True)
        # `from None` drops the original persistence error from the chain so its
        # (possibly sensitive) detail never reaches the client.
        raise HTTPException(
            status_code=503,
            detail=f"Failed to register suite '{name}'; cloned files were rolled back.",
        ) from None
    return LinkTestSuiteResponse(
        success=True,
        suite_name=name,
        is_accessible=suite_path.is_dir(),
        message=f"Successfully cloned '{name}'!",
    )


@router.post("/tests/{suite_name}/pull", response_model=LinkTestSuiteResponse)
async def pull_test_suite(
    suite_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> LinkTestSuiteResponse:
    """Update a git suite to its recorded ref (owner or admin only).

    Shallow clones can't ``git pull`` cleanly, so this fetches the recorded ref
    (or remote HEAD if none) at depth 1 and hard-resets to it (G3).
    """
    container = request.app.state.container
    store = container.store
    cfg = container.settings

    suite = await store.get_suite(suite_name)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"Suite '{suite_name}' not found")
    _require_owner_access(suite.created_by, current_user)
    if suite.source != "git":
        raise HTTPException(status_code=400, detail="Only git suites can be pulled")

    tests_root = Path(cfg.tests_root).resolve()
    suite_path = _safe_suite_path(tests_root, suite_name)
    ref = suite.ref or "HEAD"

    # Re-inject the credential recorded at clone time. Existing credentials are
    # re-authorized against the caller; a since-deleted credential remains
    # lenient and degrades to an unauthenticated fetch.
    secret: str | None = None
    if suite.credential_ref:
        cred = await store.get_credential(suite.credential_ref)
        if cred is not None:
            _require_owner_access(cred.created_by, current_user)
            enc = await store.get_credential_secret(suite.credential_ref)
            if enc is not None:
                secret = CredentialCipher(cfg.secret_key).decrypt(enc)

    async with _git_auth_env(secret) as env:
        # ``--`` separates the refspec from options so a ref can never be parsed
        # as a git flag (defence in depth; clone uses the same guard).
        rc, _out, err = await _run_git(
            ["fetch", "--depth", "1", "origin", "--", ref],
            cwd=str(suite_path),
            env=env,
        )
        if rc != 0:
            msg = _scrub_paths(err.strip(), str(suite_path), str(tests_root))
            raise HTTPException(status_code=502, detail=f"git fetch failed: {msg}")
        rc2, _out2, err2 = await _run_git(
            ["reset", "--hard", "FETCH_HEAD"], cwd=str(suite_path), env=env
        )
        if rc2 != 0:
            msg = _scrub_paths(err2.strip(), str(suite_path), str(tests_root))
            raise HTTPException(status_code=502, detail=f"git reset failed: {msg}")

    return LinkTestSuiteResponse(
        success=True,
        suite_name=suite_name,
        is_accessible=suite_path.is_dir(),
        message=f"Successfully pulled '{suite_name}'!",
    )


@router.post("/tests/{suite_name}/prepare", response_model=LinkTestSuiteResponse)
async def prepare_test_suite(
    suite_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> LinkTestSuiteResponse:
    """Install a git suite's node dependencies via ``npm ci`` (owner/admin only).

    Executors run with no network (SEC-3), so dependency install must happen on
    the platform, once, after clone. Idempotent and decoupled from clone (which
    can take minutes for npm). Local suites reuse host-installed deps and git
    suites without a ``package.json`` need nothing — both return success.
    """
    container = request.app.state.container
    store = container.store
    cfg = container.settings

    suite = await store.get_suite(suite_name)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"Suite '{suite_name}' not found")
    _require_owner_access(suite.created_by, current_user)
    if suite.source != "git":
        return LinkTestSuiteResponse(
            success=True,
            suite_name=suite_name,
            is_accessible=True,
            message=(
                f"'{suite_name}' is a local suite; local suites reuse host "
                "dependencies, nothing to prepare."
            ),
        )

    tests_root = Path(cfg.tests_root).resolve()
    suite_path = _safe_suite_path(tests_root, suite_name)
    if not suite_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Suite '{suite_name}' directory not found")
    if not (suite_path / "package.json").exists():
        return LinkTestSuiteResponse(
            success=True,
            suite_name=suite_name,
            is_accessible=True,
            message=f"No package.json in '{suite_name}'; nothing to prepare.",
        )

    # --ignore-scripts is mandatory (SEC): a git suite is cloned from an arbitrary
    # repo, so its package.json lifecycle scripts (preinstall/postinstall/prepare)
    # are untrusted. Plain `npm ci` would run them in the platform process — an RCE
    # that bypasses the docker-executor isolation (the platform holds the docker
    # socket). Dependency *code* still runs later, but only inside the sandboxed
    # executor, never here.
    rc, _out, err = await _run_cmd(
        ["npm", "ci", "--ignore-scripts"], cwd=str(suite_path), timeout=_NPM_TIMEOUT
    )
    if rc != 0:
        msg = _scrub_paths(err.strip(), str(suite_path), str(tests_root))
        raise HTTPException(status_code=502, detail=f"npm ci failed: {msg}")

    return LinkTestSuiteResponse(
        success=True,
        suite_name=suite_name,
        is_accessible=True,
        message=f"Dependencies installed for '{suite_name}'.",
    )


@router.delete("/tests/{suite_name}")
async def delete_test_suite(
    suite_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Remove a suite's files and metadata (owner or admin only).

    git suites are real directories (rmtree); local suites are symlinks
    (unlink). A directory present without a record (manually placed, N3) is
    admin-only to delete; an orphan record with no directory (N2) still clears.
    """
    import shutil

    container = request.app.state.container
    store = container.store
    cfg = container.settings

    suite = await store.get_suite(suite_name)
    tests_root = Path(cfg.tests_root).resolve()
    suite_path = _safe_suite_path(tests_root, suite_name)
    exists_on_disk = suite_path.is_symlink() or suite_path.exists()

    if suite is None and not exists_on_disk:
        raise HTTPException(status_code=404, detail=f"Suite '{suite_name}' not found")
    if suite is not None:
        _require_owner_access(suite.created_by, current_user)
    elif current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    if suite_path.is_symlink():
        await asyncio.to_thread(suite_path.unlink)
    elif suite_path.is_dir():
        await asyncio.to_thread(shutil.rmtree, suite_path)
    await store.delete_suite(suite_name)

    return {"status": "success", "message": f"Suite {suite_name} deleted"}


# Test-file suffixes surfaced in the file tree: pytest (.py with test_ prefix /
# _test.py suffix) plus Playwright/JS-TS specs (*.spec.* / *.test.*). Without the
# JS/TS suffixes a Playwright suite (e.g. my-e2e-suite) renders an empty
# tree and the UI can't browse or select any test.
_JS_TEST_SUFFIXES = (".spec.ts", ".spec.js", ".spec.mjs", ".test.ts", ".test.js", ".test.mjs")
_PLAYWRIGHT_TITLE_PATTERNS = (
    re.compile(
        r"""\btest(?:\.(?:describe(?:\.(?:only|skip|serial|parallel))?|only|skip|fixme|slow|fail))?\s*\(\s*'((?:\\.|[^'\\])*)'""",
        re.DOTALL,
    ),
    re.compile(
        r'\btest(?:\.(?:describe(?:\.(?:only|skip|serial|parallel))?|only|skip|fixme|slow|fail))?\s*\(\s*"((?:\\.|[^"\\])*)"',
        re.DOTALL,
    ),
    re.compile(
        r"""\btest(?:\.(?:describe(?:\.(?:only|skip|serial|parallel))?|only|skip|fixme|slow|fail))?\s*\(\s*`((?:\\.|[^`\\])*)`""",
        re.DOTALL,
    ),
)
_PLAYWRIGHT_TAG_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")


def _is_test_tree_file(name: str) -> bool:
    if name.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py")
    return name.endswith(_JS_TEST_SUFFIXES)


@router.get("/tests/{suite_name}/tree")
async def get_test_tree(
    request: Request,
    suite_name: str,
    _current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Recursively scan a test suite directory and return its file-tree structure."""
    cfg = request.app.state.container.settings
    from qarunner.core.paths import safe_subpath

    try:
        suite_path = safe_subpath(cfg.tests_root, suite_name)
    except UnsafePath:
        raise HTTPException(status_code=400, detail="Invalid suite path") from None

    suite_dir = Path(suite_path)
    if not suite_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Suite {suite_name} not found")

    def walk_dir(current_path: Path, base_dir: Path) -> list[dict]:
        nodes = []
        try:
            entries = sorted(
                current_path.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
            for entry in entries:
                if entry.is_symlink() or entry.name.startswith(".") or entry.name.startswith("__"):
                    continue
                relative_path = str(entry.relative_to(base_dir))
                if entry.is_dir():
                    children = walk_dir(entry, base_dir)
                    # Only include folders that (recursively) contain test files.
                    if children:
                        nodes.append(
                            {
                                "name": entry.name,
                                "path": relative_path,
                                "is_dir": True,
                                "children": children,
                            }
                        )
                elif entry.is_file() and _is_test_tree_file(entry.name):
                    nodes.append({"name": entry.name, "path": relative_path, "is_dir": False})
        except OSError:
            logger.warning("Failed to scan test directory %s", current_path, exc_info=True)
        return nodes

    return await asyncio.to_thread(walk_dir, suite_dir, suite_dir)


@router.get("/tests/{suite_name}/markers")
async def get_test_markers(
    request: Request,
    suite_name: str,
    _current_user: User = Depends(get_current_user),
) -> list[str]:
    """Statically parse pytest markers and Playwright title tags under the suite."""
    import ast

    cfg = request.app.state.container.settings
    from qarunner.core.paths import safe_subpath

    try:
        suite_path = safe_subpath(cfg.tests_root, suite_name)
    except UnsafePath:
        raise HTTPException(status_code=400, detail="Invalid suite path") from None

    suite_dir = Path(suite_path)
    if not suite_dir.is_dir():
        return []

    def _parse_markers() -> list[str]:
        markers = set()
        suite_root = suite_dir.resolve()
        for py_file in suite_dir.glob("**/*.py"):
            if py_file.name.startswith(".") or py_file.name.startswith("__"):
                continue
            if not py_file.resolve().is_relative_to(suite_root):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content, filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        for decorator in node.decorator_list:
                            dec_node = decorator
                            if isinstance(dec_node, ast.Call):
                                dec_node = dec_node.func

                            if isinstance(dec_node, ast.Attribute) and dec_node.attr != "mark":
                                inner = dec_node.value
                                if isinstance(inner, ast.Attribute) and inner.attr == "mark":
                                    val_inner = inner.value
                                    if (
                                        isinstance(val_inner, ast.Name)
                                        and val_inner.id == "pytest"
                                    ):
                                        markers.add(dec_node.attr)
            except (OSError, SyntaxError, ValueError):
                logger.warning("Failed to parse markers from %s", py_file, exc_info=True)
        for js_file in suite_dir.glob("**/*"):
            if js_file.name.startswith(".") or js_file.name.startswith("__"):
                continue
            if not js_file.is_file() or not js_file.name.endswith(_JS_TEST_SUFFIXES):
                continue
            content = js_file.read_text(encoding="utf-8", errors="replace")
            for title_pattern in _PLAYWRIGHT_TITLE_PATTERNS:
                for title_match in title_pattern.finditer(content):
                    markers.update(_PLAYWRIGHT_TAG_PATTERN.findall(title_match.group(1)))
        return sorted(list(markers))

    return await asyncio.to_thread(_parse_markers)


@router.post("/profiles", status_code=201, response_model=TestProfileResponse)
async def create_profile(
    req: TestProfileCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestProfileResponse:
    """Create and persist a new named execution profile."""
    container = request.app.state.container
    profile = await container.profile_service.create(req, created_by=current_user.username)
    return profile_to_response(profile)


@router.get("/profiles", response_model=list[TestProfileResponse])
async def list_profiles(
    request: Request,
    tests_path: str | None = None,
    current_user: User = Depends(get_current_user),
) -> list[TestProfileResponse]:
    """List execution profiles, optionally filtered by tests_path.

    Non-admins see only the profiles they created (object-level authz).
    """
    container = request.app.state.container
    profiles = await container.store.list_profiles(tests_path)
    if current_user.role != UserRole.ADMIN:
        profiles = [p for p in profiles if p.created_by == current_user.username]
    return [profile_to_response(p) for p in profiles]


@router.post("/profiles/{profile_id}/trigger", status_code=202, response_model=RunResponse)
async def trigger_profile(
    profile_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Trigger a run directly from a saved execution profile.

    The server rebuilds the RunRequest from the stored profile so the run is
    profile-bound and cannot drift from the saved runner/files/markers/env.
    """
    container = request.app.state.container
    profile = await container.store.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
    _require_owner_access(profile.created_by, current_user)

    run = await _create_run_guarded(
        container,
        RunRequest.from_profile(profile),
        current_user,
        profile_id=profile.id,
    )
    return run_to_response(run)


@router.put("/profiles/{profile_id}", response_model=TestProfileResponse)
async def update_profile(
    profile_id: str,
    req: TestProfileUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestProfileResponse:
    """Update an existing execution profile (owner or admin only)."""
    container = request.app.state.container
    # Owner check before mutating; a missing profile falls through so the service
    # raises ProfileNotFound (preserving the 404 path and its coverage).
    existing = await container.store.get_profile(profile_id)
    if existing is not None:
        _require_owner_access(existing.created_by, current_user)
    try:
        updated = await container.profile_service.update(profile_id, req)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found") from None
    return profile_to_response(updated)


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete an execution profile (owner or admin only)."""
    container = request.app.state.container
    existing = await container.store.get_profile(profile_id)
    if existing is not None:
        _require_owner_access(existing.created_by, current_user)
    try:
        await container.profile_service.delete(profile_id)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found") from None
    return {"status": "success", "message": f"Profile {profile_id} deleted"}


async def _create_run_guarded(
    container: Container,
    req: RunRequest,
    current_user: User,
    profile_id: str | None = None,
) -> Run:
    """Shared run-creation chokepoint: per-user in-flight cap (P2-7) +
    orchestrator error mapping.

    POST /runs, POST /runs/{id}/rerun and POST /schedules/{id}/trigger all funnel
    through here so a new run-creating caller can't silently bypass the guards.
    """
    # Per-user in-flight cap (P2-7): bound unbounded run accumulation by one
    # authenticated user. Admins are exempt; a limit of 0 disables the check.
    limit = container.settings.max_inflight_runs_per_user
    if limit and current_user.role != UserRole.ADMIN:
        inflight = await container.store.count_inflight_runs(current_user.username)
        if inflight >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many in-flight runs; wait for existing runs to finish.",
            )
    try:
        return await container.orchestrator.create(
            req,
            created_by=current_user.username,
            profile_id=profile_id,
        )
    except UnknownRunner as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except UnsafePath as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/runs", status_code=202, response_model=RunResponse)
async def create_run(
    req: RunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Create a new test run and return its initial state."""
    container = request.app.state.container
    run = await _create_run_guarded(container, req, current_user)
    return run_to_response(run)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunListResponse:
    """List runs, newest first (non-admins see only their own)."""
    container = request.app.state.container
    runs = await container.store.list()
    if current_user.role != UserRole.ADMIN:
        runs = [r for r in runs if r.created_by == current_user.username]
    return RunListResponse(runs=[run_to_response(r) for r in runs])


@router.get("/runs/trend", response_model=RunTrendResponse)
async def get_runs_trend(
    request: Request,
    tests_path: str,
    profile_id: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
) -> RunTrendResponse:
    """Pass-rate trend for a suite across its runs (cross-run stage 1).

    Declared before ``/runs/{run_id}`` so the literal ``trend`` segment isn't
    captured as a run id. Owner-scoped like GET /runs (non-admins see only their
    own runs); points are COMPLETED runs carrying a summary, oldest-first.
    """
    container = request.app.state.container
    runs = await container.store.list()
    if current_user.role != UserRole.ADMIN:
        runs = [r for r in runs if r.created_by == current_user.username]
    if profile_id is not None:
        runs = [r for r in runs if r.profile_id == profile_id]
    points = trend.trend_points(runs, tests_path, limit)
    return RunTrendResponse(tests_path=tests_path, points=points)


@router.get("/metrics", response_model=MetricsSummary)
async def get_metrics(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> MetricsSummary:
    """Dashboard-level quality metrics (pass rate, flaky, duration, volume).

    Computes server-side aggregates from the runs and run_test_cases tables.
    Owner-scoped: non-admins see only their own runs.
    """
    container = request.app.state.container
    runs = await container.store.list()
    owner = current_user.username if current_user.role != UserRole.ADMIN else None
    if owner is not None:
        runs = [r for r in runs if r.created_by == owner]

    now = datetime.now(UTC)
    since_7d = now - timedelta(days=7)

    # ── 7-day window: completed runs with a summary ──
    completed_7d = [
        r
        for r in runs
        if r.status == RunStatus.COMPLETED
        and r.finished_at is not None
        and r.finished_at >= since_7d
    ]
    with_summary = [r for r in completed_7d if r.summary is not None]

    pass_rate_7d = 0.0
    avg_duration_ms_7d = 0.0
    if with_summary:
        passed_count = sum(1 for r in with_summary if r.summary.pass_rate >= 1.0)  # type: ignore[union-attr]
        pass_rate_7d = passed_count / len(with_summary)
        durations = [
            r.summary.duration_ms
            for r in with_summary
            if r.summary.duration_ms > 0  # type: ignore[union-attr]
        ]
        avg_duration_ms_7d = sum(durations) / len(durations) if durations else 0.0

    # ── Flaky count (30-day window) ──
    flaky_policy = flaky.FlakyPolicy(
        min_observations=container.settings.flaky_min_observations,
        flip_threshold=container.settings.flaky_flip_threshold,
    )
    flaky_count = await container.store.count_flaky_tests(
        days=30,
        created_by=owner,
        min_observations=flaky_policy.min_observations,
        flip_threshold=flaky_policy.flip_threshold,
    )

    # ── Per-suite breakdown (all completed runs, not just 7-day) ──
    suite_map: dict[str, list[Run]] = {}
    for r in runs:
        if r.status == RunStatus.COMPLETED:
            suite_map.setdefault(r.tests_path, []).append(r)

    suites: list[SuiteMetrics] = []
    for tp, suite_runs in sorted(suite_map.items()):
        sw = [r for r in suite_runs if r.finished_at and r.finished_at >= since_7d]
        sw_summary = [r for r in sw if r.summary is not None]
        sr = 0.0
        ad = 0.0
        if sw_summary:
            sr = sum(1 for r in sw_summary if r.summary.pass_rate >= 1.0) / len(sw_summary)  # type: ignore[union-attr]
            durs = [r.summary.duration_ms for r in sw_summary if r.summary.duration_ms > 0]  # type: ignore[union-attr]
            ad = sum(durs) / len(durs) if durs else 0.0
        last = max((r.finished_at for r in suite_runs if r.finished_at), default=None)
        suites.append(
            SuiteMetrics(
                tests_path=tp,
                total_runs=len(sw),
                pass_rate=round(sr, 4),
                avg_duration_ms=round(ad, 1),
                last_run_at=last,
            )
        )

    return MetricsSummary(
        window_days=7,
        total_runs=len(runs),
        completed_runs=len(completed_7d),
        pass_rate_7d=round(pass_rate_7d, 4),
        avg_duration_ms_7d=round(avg_duration_ms_7d, 1),
        flaky_count_30d=flaky_count,
        run_volume_7d=len(completed_7d),
        suites=suites,
    )


_RUN_LOG_MAX_BYTES = 256 * 1024


def _read_log_tail(path: Path) -> str | None:
    """Return the bounded tail of a log file for API display (SEC-8).

    Reads at most ``_RUN_LOG_MAX_BYTES`` from the end so a multi-GB log can't
    exhaust memory or bloat the response. Returns ``None`` if it can't be read.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > _RUN_LOG_MAX_BYTES:
                f.seek(size - _RUN_LOG_MAX_BYTES)
            data = f.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    if size > _RUN_LOG_MAX_BYTES:
        return "[... earlier output truncated ...]\n" + text
    return text


def _safe_run_log_file(run_dir: Path, name: str) -> Path | None:
    candidate = run_dir / name
    if not candidate.exists():
        return None
    try:
        run_root = run_dir.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(run_root) or not candidate.is_file():
        return None
    return candidate


def _run_artifacts_dir(artifacts_root: str, run_id: str) -> Path:
    """Return the Playwright output directory for a run."""
    return Path(artifacts_root) / run_id / "results" / "playwright-results"


def _artifact_content_type(path: Path) -> str:
    """Best-effort browser hint for downloadable runner artifacts."""
    content_type, _encoding = mimetypes.guess_type(path.name)
    return content_type or "application/octet-stream"


def _iter_run_artifact_files(artifact_root: Path) -> list[tuple[Path, str]]:
    """Return safe artifact files as ``(path, relative_posix_path)`` tuples."""
    artifact_root_resolved = artifact_root.resolve()
    artifacts: list[tuple[Path, str]] = []
    for candidate in sorted(artifact_root.rglob("*")):
        resolved = candidate.resolve()
        if not resolved.is_relative_to(artifact_root_resolved) or not candidate.is_file():
            continue
        artifacts.append((candidate, candidate.relative_to(artifact_root).as_posix()))
    return artifacts


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Retrieve a single run by ID."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    cfg = container.settings
    run_dir = Path(cfg.artifacts_root) / run_id
    stdout_file = _safe_run_log_file(run_dir, "stdout.log")
    stderr_file = _safe_run_log_file(run_dir, "stderr.log")

    if stdout_file is None:
        run_dir_fallback = Path("./artifacts") / run_id
        stdout_fallback = _safe_run_log_file(run_dir_fallback, "stdout.log")
        if stdout_fallback is not None:
            stdout_file = stdout_fallback
            stderr_file = _safe_run_log_file(run_dir_fallback, "stderr.log")

    def _read_logs(
        stdout_path: Path | None, stderr_path: Path | None
    ) -> tuple[str | None, str | None]:
        stdout_val = _read_log_tail(stdout_path) if stdout_path is not None else None
        stderr_val = _read_log_tail(stderr_path) if stderr_path is not None else None
        return stdout_val, stderr_val

    stdout_content, stderr_content = await asyncio.to_thread(_read_logs, stdout_file, stderr_file)
    cases = await container.store.get_cases_for_run(run_id)

    res = run_to_response(run)
    res.stdout = stdout_content
    res.stderr = stderr_content
    res.cases = cases
    return res


@router.get("/runs/{run_id}/diff", response_model=RunDiffResponse)
async def get_run_diff(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunDiffResponse:
    """Diff a run's per-case results against its baseline (cross-run stage 2).

    The baseline is the most recent COMPLETED run of the same execution scope
    preceding this one (see ``regression.select_baseline``). When none exists
    the response carries ``baseline: null`` and an empty diff rather than
    flooding every case into ``new_cases``.
    """
    container = request.app.state.container
    try:
        head = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(head, current_user)

    all_runs = await container.store.list()
    # Owner-scope the baseline candidates exactly like GET /runs and
    # /runs/trend: a non-admin must not get another user's run picked as the
    # baseline, which would leak that run's id + per-case results through the
    # diff. Admins span all owners.
    if current_user.role != UserRole.ADMIN:
        all_runs = [r for r in all_runs if r.created_by == current_user.username]
    baseline = regression.select_baseline(head, all_runs)
    if baseline is None:
        return RunDiffResponse(baseline=None)

    base_cases = await container.store.get_cases_for_run(baseline.id)
    head_cases = await container.store.get_cases_for_run(head.id)
    return RunDiffResponse(
        baseline=RunDiffBaselineInfo(
            id=baseline.id,
            created_at=baseline.created_at,
            status=baseline.status,
        ),
        diff=regression.diff(base_cases, head_cases),
    )


@router.get("/cases/history", response_model=CaseHistoryResponse)
async def get_case_history(
    request: Request,
    tests_path: str,
    suite: str,
    name: str,
    profile_id: str | None = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
) -> CaseHistoryResponse:
    """A single test case's recent outcomes + flaky verdict (cross-run stage 3).

    Owner-scoped: a non-admin sees only their own runs' history; an admin spans
    all owners. Points are oldest-first; ``flaky`` counts pass<->fail flips.
    """
    container = request.app.state.container
    created_by = None if current_user.role == UserRole.ADMIN else current_user.username
    points = await container.store.get_case_history(
        tests_path,
        suite,
        name,
        limit,
        created_by,
        profile_id,
    )
    policy = flaky.FlakyPolicy(
        min_observations=container.settings.flaky_min_observations,
        flip_threshold=container.settings.flaky_flip_threshold,
    )
    is_flaky, flips = flaky.flakiness([p.status for p in points], policy=policy)
    return CaseHistoryResponse(points=points, flaky=is_flaky, flip_count=flips)


def _resolve_report_file(report: ReportRef) -> Path:
    """Resolve an Allure HTML entrypoint without allowing report path escape."""
    from qarunner.core.paths import safe_subpath

    try:
        return Path(safe_subpath(report.allure_results_dir, report.allure_report_file))
    except UnsafePath:
        raise HTTPException(status_code=403, detail="Access denied") from None


@router.get("/runs/{run_id}/report")
async def get_report(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve the HTML report for a completed run."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)
    if not run.report or not run.report.html_generated or not run.report.allure_report_file:
        raise HTTPException(status_code=404, detail="Report not available")
    report_file = _resolve_report_file(run.report)
    return FileResponse(report_file, media_type="text/html")


@router.get("/runs/{run_id}/report/{path:path}")
async def get_report_assets(
    run_id: str,
    path: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve Allure report static assets (JS, CSS, data files) securely."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)
    if not run.report or not run.report.html_generated or not run.report.allure_report_file:
        raise HTTPException(status_code=404, detail="Report not available")

    from qarunner.core.paths import safe_subpath

    report_file = _resolve_report_file(run.report)
    allure_report_dir = str(report_file.parent)
    try:
        asset_path = safe_subpath(allure_report_dir, path)  # raises UnsafePath on escape
    except UnsafePath:
        raise HTTPException(status_code=403, detail="Access denied") from None

    asset_file = Path(asset_path)
    if not asset_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(asset_file)


@router.get("/runs/{run_id}/artifacts", response_model=RunArtifactListResponse)
async def list_run_artifacts(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunArtifactListResponse:
    """List downloadable runner artifacts for a completed run."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    artifact_root = _run_artifacts_dir(container.settings.artifacts_root, run_id)
    if not artifact_root.is_dir():
        return RunArtifactListResponse()

    artifacts: list[RunArtifactResponse] = []
    for candidate, relative_path in _iter_run_artifact_files(artifact_root):
        artifacts.append(
            RunArtifactResponse(
                path=relative_path,
                size_bytes=candidate.stat().st_size,
                content_type=_artifact_content_type(candidate),
            )
        )
    return RunArtifactListResponse(artifacts=artifacts)


@router.get("/runs/{run_id}/artifacts.zip")
async def download_run_artifacts_archive(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Download all runner artifacts for a run as a zip archive."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    artifact_root = _run_artifacts_dir(container.settings.artifacts_root, run_id)
    if not artifact_root.is_dir():
        raise HTTPException(status_code=404, detail="Artifacts not available")

    payload = io.BytesIO()
    with ZipFile(payload, mode="w", compression=ZIP_DEFLATED) as archive:
        for artifact_file, relative_path in _iter_run_artifact_files(artifact_root):
            archive.write(artifact_file, arcname=relative_path)

    return Response(
        content=payload.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-artifacts.zip"'},
    )


@router.get("/runs/{run_id}/artifacts/{path:path}")
async def download_run_artifact(
    run_id: str,
    path: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Download a single runner artifact without allowing path escape."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    from qarunner.core.paths import safe_subpath

    artifact_root = _run_artifacts_dir(container.settings.artifacts_root, run_id)
    try:
        artifact_path = safe_subpath(str(artifact_root), path)
    except UnsafePath:
        raise HTTPException(status_code=403, detail="Access denied") from None

    artifact_file = Path(artifact_path)
    if not artifact_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(artifact_file)


# CONC-1: bound the SSE log-follow loop so an abandoned or slow client cannot
# pin a worker forever, and cap the final flush so a multi-GB log can't be read
# into memory all at once.
_SSE_MAX_FOLLOW_SECONDS = 3600.0
_SSE_MAX_TAIL_BYTES = 256 * 1024


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream stdout logs in real-time using Server-Sent Events (SSE)."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    import asyncio

    cfg = container.settings
    stdout_file = Path(cfg.artifacts_root) / run_id / "stdout.log"
    terminal_states = (
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.TIMEOUT,
        RunStatus.CANCELLED,
    )

    async def event_generator():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SSE_MAX_FOLLOW_SECONDS
        # Wait up to 5 seconds for the file to be created initially
        for _ in range(50):
            if await request.is_disconnected():
                return
            if stdout_file.exists():
                break
            try:
                run = await container.store.get(run_id)
                if run.status in terminal_states:
                    break
            except RunNotFound:
                break
            await asyncio.sleep(0.1)

        if not stdout_file.exists():
            yield "data: [System] Log file not found.\n\n"
            return

        # Non-blocking IO + bounded lifetime: file ops run off the event loop so
        # a slow disk or huge file can't stall it, and we stop following once the
        # client disconnects or the max duration is reached (CONC-1).
        f = await asyncio.to_thread(open, stdout_file, "r", encoding="utf-8", errors="replace")
        try:
            while True:
                if await request.is_disconnected():
                    break
                if loop.time() > deadline:
                    yield "data: [System] Log stream closed (max duration reached).\n\n"
                    break
                line = await asyncio.to_thread(f.readline)
                if line:
                    yield f"data: {line.rstrip('\r\n')}\n\n"
                    await asyncio.sleep(0.01)
                else:
                    try:
                        run = await container.store.get(run_id)
                        if run.status in terminal_states:
                            # Flush remaining lines, bounded so a multi-GB tail
                            # is never read into memory in one shot.
                            remaining = await asyncio.to_thread(f.read, _SSE_MAX_TAIL_BYTES)
                            if remaining:
                                for log_line in remaining.splitlines():
                                    yield f"data: {log_line}\n\n"
                            break
                    except Exception:
                        # Resilient streaming: any mid-stream failure (run deleted,
                        # log read error, …) ends the stream cleanly rather than
                        # 500-ing a half-sent response.
                        logger.warning(
                            "Log stream for run %s ended on error", run_id, exc_info=True
                        )
                        break
                    await asyncio.sleep(0.2)
        finally:
            await asyncio.to_thread(f.close)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.put("/runs/{run_id}/lock", response_model=RunResponse)
async def lock_run(
    run_id: str,
    req: LockRunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Toggle lock/pin status of a run to protect it from deletion."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)

    await container.store.lock_run(run_id, req.locked)
    updated_run = await container.store.get(run_id)
    return run_to_response(updated_run)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Cancel a queued or running run (owner/admin). Terminal runs return 409."""
    container = request.app.state.container
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)
    if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail="Run is not in a cancellable state (already finished).",
        )
    cancelled = await container.orchestrator.cancel(run_id)
    return run_to_response(cancelled)


@router.delete("/runs/{run_id}")
async def delete_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a run's metadata and physical artifacts (owner/admin).

    A locked run is protected and a queued/running run must be cancelled first —
    both return 409. Artifact removal is best-effort (``ignore_errors``): a run
    the caller asked to delete always leaves the store, even if its directory
    can't be removed, so deletion can't strand a row pointing at gone files.
    """
    container = request.app.state.container
    cfg = container.settings
    try:
        run = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(run, current_user)
    if run.locked:
        raise HTTPException(status_code=409, detail="Run is locked; unlock it before deleting.")
    if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        raise HTTPException(
            status_code=409, detail="Run is still active; cancel it before deleting."
        )

    import shutil

    run_dir = Path(cfg.artifacts_root) / run_id
    if run_dir.exists():
        await asyncio.to_thread(shutil.rmtree, run_dir, ignore_errors=True)
    await container.store.delete_run(run_id)
    return {"status": "success", "message": f"Run {run_id} deleted"}


@router.post("/runs/{run_id}/rerun", status_code=202, response_model=RunResponse)
async def rerun_run(
    run_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Re-run a run with the same parameters as a brand-new run (owner/admin).

    The original is left untouched; the new run is attributed to the caller, so a
    re-run of a scheduled run lands in the caller's own list rather than under
    ``system:schedule``.
    """
    container = request.app.state.container
    try:
        original = await container.store.get(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from None
    _require_run_access(original, current_user)
    new_run = await _create_run_guarded(
        container,
        RunRequest.from_run(original),
        current_user,
        profile_id=original.profile_id,
    )
    return run_to_response(new_run)


@router.post("/runs/cleanup")
async def cleanup_runs(
    request: Request,
    retention_days: int = Query(30, ge=1),
    _current_admin: User = Depends(get_current_admin),
):
    """Clean up physical run artifacts older than X days, preserving SQLite metadata."""
    container = request.app.state.container
    runs_to_cleanup = await container.store.get_old_unlocked_runs(retention_days)

    import asyncio
    import shutil

    cfg = container.settings
    cleaned_count = 0

    for r in runs_to_cleanup:
        # Re-read the lock just before deleting: a run locked after it was
        # selected as unlocked (TOCTOU) must be preserved. Run metadata is never
        # deleted, so get() always resolves.
        if (await container.store.get(r.id)).locked:
            continue
        run_dir = Path(cfg.artifacts_root) / r.id
        if run_dir.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, run_dir)
                # Set report reference to None in DB to prevent broken links
                updated_run = r.model_copy(update={"report": None})
                await container.store.save(updated_run)
                cleaned_count += 1
            except OSError:
                logger.warning("Failed to remove run directory %s", run_dir, exc_info=True)

    return {"status": "success", "cleaned_runs": cleaned_count}


# ── Test Scheduling Endpoints (Secured with JWT) ─────────────────────────


@router.get("/schedules/preview", response_model=TestSchedulePreviewResponse)
async def preview_schedule(
    expression: str,
    timezone: str = "UTC",
    _current_user: User = Depends(get_current_user),
) -> TestSchedulePreviewResponse:
    """Preview the next 5 occurrences of a cron expression."""
    from qarunner.core import cron

    if not cron.is_valid_timezone(timezone):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}")

    try:
        if not cron.is_valid_cron(expression):
            raise ValueError("Invalid cron expression syntax")
        return TestSchedulePreviewResponse(next_runs=cron.next_runs(expression, timezone, 5))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {str(e)}") from e


@router.post("/schedules", status_code=201, response_model=TestScheduleResponse)
async def create_schedule(
    req: TestScheduleCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Create and persist a new test schedule."""
    container = request.app.state.container
    await _require_profile_access(container, req.profile_id, current_user)
    try:
        schedule = await container.schedule_service.create(req, created_by=current_user.username)
    except InvalidScheduleRequest as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return schedule_to_response(schedule)


@router.get("/schedules", response_model=list[TestScheduleResponse])
async def list_schedules(
    request: Request,
    profile_id: str | None = None,
    current_user: User = Depends(get_current_user),
) -> list[TestScheduleResponse]:
    """List test schedules, optionally filtered by profile_id.

    Non-admins see only the schedules they created (object-level authz).
    """
    container = request.app.state.container
    schedules = await container.store.list_schedules(profile_id)
    if current_user.role != UserRole.ADMIN:
        schedules = [s for s in schedules if s.created_by == current_user.username]
    return [schedule_to_response(s) for s in schedules]


@router.get("/schedules/{schedule_id}", response_model=TestScheduleResponse)
async def get_schedule(
    schedule_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Retrieve a single test schedule by ID (owner or admin only)."""
    container = request.app.state.container
    schedule = await container.store.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    _require_owner_access(schedule.created_by, current_user)
    return schedule_to_response(schedule)


@router.put("/schedules/{schedule_id}", response_model=TestScheduleResponse)
async def update_schedule(
    schedule_id: str,
    req: TestScheduleUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Update an existing test schedule (owner or admin only)."""
    container = request.app.state.container
    # Owner check before mutating; a missing schedule falls through so the
    # service raises ScheduleNotFound (preserving the 404 path and its coverage).
    existing = await container.store.get_schedule(schedule_id)
    if existing is not None:
        _require_owner_access(existing.created_by, current_user)
        await _require_profile_access(container, req.profile_id, current_user)
    try:
        updated = await container.schedule_service.update(schedule_id, req)
    except ScheduleNotFound:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found") from None
    except InvalidScheduleRequest as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return schedule_to_response(updated)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a test schedule (owner or admin only)."""
    container = request.app.state.container
    existing = await container.store.get_schedule(schedule_id)
    if existing is not None:
        _require_owner_access(existing.created_by, current_user)
    try:
        await container.schedule_service.delete(schedule_id)
    except ScheduleNotFound:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found") from None
    return {"status": "success", "message": f"Schedule {schedule_id} deleted"}


@router.post("/schedules/{schedule_id}/trigger", status_code=202, response_model=RunResponse)
async def trigger_schedule(
    schedule_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Fire a schedule's profile as a run right now (owner/admin).

    Unlike the cron path this skips ``claim_schedule_run`` (there's no fire-time
    to deduplicate — a manual press should always run) and attributes the run to
    the user who pressed it, not ``system:schedule``, so it lands in their own
    run list.
    """
    container = request.app.state.container
    store = container.store
    schedule = await store.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    _require_owner_access(schedule.created_by, current_user)
    profile = await store.get_profile(schedule.profile_id)
    if profile is None:
        raise HTTPException(
            status_code=409,
            detail=f"Schedule's profile '{schedule.profile_id}' no longer exists.",
        )
    _require_owner_access(profile.created_by, current_user)
    run = await _create_run_guarded(
        container,
        RunRequest.from_profile(profile),
        current_user,
        profile_id=profile.id,
    )
    return run_to_response(run)
