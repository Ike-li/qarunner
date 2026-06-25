"""FastAPI routes — thin HTTP layer over the orchestrator / store / auth."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from qarunner.api.deps import get_current_admin, get_current_user
from qarunner.api.schemas import (
    LockRunRequest,
    LoginRequest,
    RunListResponse,
    RunResponse,
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
    CloneTestSuiteRequest,
    LinkTestSuiteRequest,
    LinkTestSuiteResponse,
    profile_to_response,
    run_to_response,
    schedule_to_response,
)
from qarunner.core.auth import create_access_token, hash_password, verify_password
from qarunner.errors import (
    InvalidScheduleRequest,
    LoginLockedOut,
    ProfileNotFound,
    RunNotFound,
    ScheduleNotFound,
    UnknownRunner,
    UnsafePath,
)
from qarunner.models import Run, RunRequest, TestSuite, User, UserRole

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


async def _run_git(
    args: list[str], *, cwd: str | None = None, timeout: float = _GIT_TIMEOUT
) -> tuple[int, str, str]:
    """Run ``git <args>`` with a parametrised argv (no shell) under a timeout.

    Returns ``(returncode, stdout, stderr)``. The argv form (never a shell
    string) keeps repo URLs / refs from being interpreted as commands. On
    timeout the process is killed and a non-zero code is synthesised so callers
    handle it exactly like any other git failure.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "git operation timed out"
    return proc.returncode, out_b.decode(errors="replace"), err_b.decode(errors="replace")


def _validate_git_url(url: str) -> None:
    """Reject repo URLs outside the allowlist (SSRF / local-file / command exec).

    Only ``https://`` and scp-style ``git@`` are accepted; ``file://``, ``ext::``,
    plain ``http://`` and anything else are refused with 400.
    """
    if url.startswith("https://") or url.startswith("git@"):
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
    token = create_access_token(
        user_record["username"], user_record["role"], container.settings
    )
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

    def _scan_tests() -> list[str]:
        paths: list[str] = []
        for entry in tests_root.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("__"):
                paths.append(entry.name)
        paths.sort()
        return paths

    return await asyncio.to_thread(_scan_tests)


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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create tests_root directory: {str(e)}",
            )

    target_path = Path(payload.path)
    suite_name = target_path.name
    if not suite_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: unable to extract directory name.",
        )

    link_path = tests_root / suite_name

    # Clear existing if it exists
    if link_path.exists() or link_path.is_symlink():
        try:
            if link_path.is_symlink() or link_path.is_file():
                link_path.unlink()
            else:
                shutil.rmtree(link_path)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to clear existing test suite entry: {str(e)}",
            )

    try:
        os.symlink(payload.path, link_path)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create symlink: {str(e)}",
        )

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

    args = ["clone", "--depth", "1"]
    if payload.ref:
        args += ["-b", payload.ref]
    args += ["--", payload.url, str(suite_path)]
    rc, _out, err = await _run_git(args)
    if rc != 0:
        raise HTTPException(status_code=502, detail=f"git clone failed: {err.strip()}")

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

    rc, _out, err = await _run_git(
        ["fetch", "--depth", "1", "origin", ref], cwd=str(suite_path)
    )
    if rc != 0:
        raise HTTPException(status_code=502, detail=f"git fetch failed: {err.strip()}")
    rc2, _out2, err2 = await _run_git(
        ["reset", "--hard", "FETCH_HEAD"], cwd=str(suite_path)
    )
    if rc2 != 0:
        raise HTTPException(status_code=502, detail=f"git reset failed: {err2.strip()}")

    return LinkTestSuiteResponse(
        success=True,
        suite_name=suite_name,
        is_accessible=suite_path.is_dir(),
        message=f"Successfully pulled '{suite_name}'!",
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
    except UnsafePath as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

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
                if entry.name.startswith(".") or entry.name.startswith("__"):
                    continue
                relative_path = str(entry.relative_to(base_dir))
                if entry.is_dir():
                    children = walk_dir(entry, base_dir)
                    # Only include folders if they contain python files (recursively)
                    if children:
                        nodes.append({
                            "name": entry.name,
                            "path": relative_path,
                            "is_dir": True,
                            "children": children
                        })
                elif entry.is_file() and entry.suffix == ".py":
                    if entry.name.startswith("test_") or entry.name.endswith("_test.py"):
                        nodes.append({
                            "name": entry.name,
                            "path": relative_path,
                            "is_dir": False
                        })
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
    """Statically parse pytest decorators under the suite using Python's AST."""
    import ast
    cfg = request.app.state.container.settings
    from qarunner.core.paths import safe_subpath
    try:
        suite_path = safe_subpath(cfg.tests_root, suite_name)
    except UnsafePath as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    suite_dir = Path(suite_path)
    if not suite_dir.is_dir():
        return []

    def _parse_markers() -> list[str]:
        markers = set()
        for py_file in suite_dir.glob("**/*.py"):
            if py_file.name.startswith(".") or py_file.name.startswith("__"):
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
                                    if isinstance(val_inner, ast.Name) and val_inner.id == "pytest":
                                        markers.add(dec_node.attr)
            except (OSError, SyntaxError, ValueError):
                logger.warning("Failed to parse markers from %s", py_file, exc_info=True)
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


@router.post("/runs", status_code=202, response_model=RunResponse)
async def create_run(
    req: RunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Create a new test run and return its initial state."""
    container = request.app.state.container
    if (
        req.executor_mode == "subprocess"
        and current_user.role != UserRole.ADMIN
        and not container.settings.allow_subprocess_for_non_admins
    ):
        raise HTTPException(
            status_code=400,
            detail="Subprocess execution mode is restricted to administrators.",
        )
    try:
        run = await container.orchestrator.create(req, created_by=current_user.username)
    except UnknownRunner as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except UnsafePath as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    stdout_file = run_dir / "stdout.log"
    stderr_file = run_dir / "stderr.log"

    if not stdout_file.exists():
        run_dir_fallback = Path("./artifacts") / run_id
        if (run_dir_fallback / "stdout.log").exists():
            stdout_file = run_dir_fallback / "stdout.log"
            stderr_file = run_dir_fallback / "stderr.log"

    def _read_logs(stdout_path: Path, stderr_path: Path) -> tuple[str | None, str | None]:
        stdout_val = _read_log_tail(stdout_path) if stdout_path.exists() else None
        stderr_val = _read_log_tail(stderr_path) if stderr_path.exists() else None
        return stdout_val, stderr_val

    stdout_content, stderr_content = await asyncio.to_thread(_read_logs, stdout_file, stderr_file)


    res = run_to_response(run)
    res.stdout = stdout_content
    res.stderr = stderr_content
    return res



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
    return FileResponse(run.report.allure_report_file, media_type="text/html")


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

    allure_report_dir = str(Path(run.report.allure_report_file).parent)
    try:
        asset_path = safe_subpath(allure_report_dir, path)  # raises UnsafePath on escape
    except UnsafePath:
        raise HTTPException(status_code=403, detail="Access denied") from None

    asset_file = Path(asset_path)
    if not asset_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(asset_file)


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

    from qarunner.models import RunStatus

    cfg = container.settings
    stdout_file = Path(cfg.artifacts_root) / run_id / "stdout.log"
    terminal_states = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TIMEOUT)

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
        f = await asyncio.to_thread(
            open, stdout_file, "r", encoding="utf-8", errors="replace"
        )
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


@router.post("/runs/cleanup")
async def cleanup_runs(
    request: Request,
    retention_days: int = 30,
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

