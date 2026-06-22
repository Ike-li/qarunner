"""FastAPI routes — thin HTTP layer over the orchestrator / store / auth."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from qarunner.api.deps import get_current_admin, get_current_user
from qarunner.api.schemas import (
    LoginRequest,
    RunListResponse,
    RunResponse,
    TokenResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    run_to_response,
    TestProfileResponse,
    TestProfileCreateRequest,
    TestProfileUpdateRequest,
    profile_to_response,
    TestScheduleResponse,
    TestScheduleCreateRequest,
    TestScheduleUpdateRequest,
    TestSchedulePreviewResponse,
    schedule_to_response,
    LockRunRequest,
)
from qarunner.config import Settings
from qarunner.core.auth import create_access_token, hash_password, verify_password
from qarunner.errors import RunNotFound, UnknownRunner, UnsafePath
from qarunner.models import Run, RunRequest, TestProfile, TestSchedule, User, UserRole

router = APIRouter()


def _require_run_access(run: Run, user: User) -> None:
    """Raise 403 unless *user* owns *run* or is an admin (object-level authz)."""
    if user.role != UserRole.ADMIN and run.created_by != user.username:
        raise HTTPException(status_code=403, detail="Access denied")


# ── Auth & User Management Endpoints ─────────────────────────────────────


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    """Authenticate credentials and return a JWT access token."""
    container = request.app.state.container
    user_record = await container.store.get_user(req.username)
    if not user_record or not verify_password(req.password, user_record["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_access_token(user_record["username"], user_record["role"])
    return TokenResponse(access_token=token)


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

    # Fetch newly created user record to construct response
    new_user = await container.store.get_user(req.username)
    assert new_user is not None
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
async def list_tests(_current_user: User = Depends(get_current_user)) -> list[str]:
    """List all available test directories directly under tests_root."""
    cfg = Settings()
    tests_root = Path(cfg.tests_root).resolve()
    if not tests_root.is_dir():
        return []

    paths: list[str] = []
    for entry in tests_root.iterdir():
        if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("__"):
            paths.append(entry.name)
    paths.sort()
    return paths


@router.get("/tests/{suite_name}/tree")
async def get_test_tree(
    suite_name: str,
    _current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Recursively scan a test suite directory and return its file-tree structure."""
    cfg = Settings()
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
            entries = sorted(list(current_path.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
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
        except Exception:
            pass
        return nodes

    return walk_dir(suite_dir, suite_dir)


@router.get("/tests/{suite_name}/markers")
async def get_test_markers(
    suite_name: str,
    _current_user: User = Depends(get_current_user),
) -> list[str]:
    """Statically parse pytest decorators under the suite using Python's AST."""
    import ast
    cfg = Settings()
    from qarunner.core.paths import safe_subpath
    try:
        suite_path = safe_subpath(cfg.tests_root, suite_name)
    except UnsafePath as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    suite_dir = Path(suite_path)
    if not suite_dir.is_dir():
        return []

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
        except Exception:
            pass

    return sorted(list(markers))


@router.post("/profiles", status_code=201, response_model=TestProfileResponse)
async def create_profile(
    req: TestProfileCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestProfileResponse:
    """Create and persist a new named execution profile."""
    container = request.app.state.container
    import uuid
    from datetime import UTC, datetime
    
    profile_id = str(uuid.uuid4())
    profile = TestProfile(
        id=profile_id,
        name=req.name,
        description=req.description,
        tests_path=req.tests_path,
        selected_files=req.selected_files,
        selected_markers=req.selected_markers,
        extra_args=req.extra_args,
        executor_mode=req.executor_mode,
        timeout=req.timeout,
        created_by=current_user.username,
        created_at=datetime.now(UTC),
        env=req.env,
    )
    await container.store.save_profile(profile)
    return profile_to_response(profile)


@router.get("/profiles", response_model=list[TestProfileResponse])
async def list_profiles(
    request: Request,
    tests_path: str | None = None,
    _current_user: User = Depends(get_current_user),
) -> list[TestProfileResponse]:
    """List execution profiles, optionally filtered by tests_path."""
    container = request.app.state.container
    profiles = await container.store.list_profiles(tests_path)
    return [profile_to_response(p) for p in profiles]


@router.put("/profiles/{profile_id}", response_model=TestProfileResponse)
async def update_profile(
    profile_id: str,
    req: TestProfileUpdateRequest,
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> TestProfileResponse:
    """Update an existing execution profile."""
    container = request.app.state.container
    existing = await container.store.get_profile(profile_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        
    updated = TestProfile(
        id=existing.id,
        name=req.name,
        description=req.description,
        tests_path=req.tests_path,
        selected_files=req.selected_files,
        selected_markers=req.selected_markers,
        extra_args=req.extra_args,
        executor_mode=req.executor_mode,
        timeout=req.timeout,
        created_by=existing.created_by,
        created_at=existing.created_at,
        env=req.env,
    )
    await container.store.save_profile(updated)
    return profile_to_response(updated)


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> dict:
    """Delete an execution profile."""
    container = request.app.state.container
    deleted = await container.store.delete_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
    return {"status": "success", "message": f"Profile {profile_id} deleted"}


@router.post("/runs", status_code=202, response_model=RunResponse)
async def create_run(
    req: RunRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> RunResponse:
    """Create a new test run and return its initial state."""
    container = request.app.state.container
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

    cfg = Settings()
    run_dir = Path(cfg.artifacts_root) / run_id
    stdout_file = run_dir / "stdout.log"
    stderr_file = run_dir / "stderr.log"

    if not stdout_file.exists():
        run_dir_fallback = Path("./artifacts") / run_id
        if (run_dir_fallback / "stdout.log").exists():
            stdout_file = run_dir_fallback / "stdout.log"
            stderr_file = run_dir_fallback / "stderr.log"

    stdout_content = None
    stderr_content = None

    if stdout_file.exists():
        stdout_content = _read_log_tail(stdout_file)
    if stderr_file.exists():
        stderr_content = _read_log_tail(stderr_file)

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
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    _require_run_access(run, current_user)

    from qarunner.models import RunStatus
    import asyncio

    cfg = Settings()
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
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
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

    import shutil
    import asyncio

    cfg = Settings()
    cleaned_count = 0

    for r in runs_to_cleanup:
        run_dir = Path(cfg.artifacts_root) / r.id
        if run_dir.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, run_dir)
                cleaned_count += 1
            except Exception:
                pass

    return {"status": "success", "cleaned_runs": cleaned_count}


# ── Test Scheduling Endpoints (Secured with JWT) ─────────────────────────


@router.get("/schedules/preview", response_model=TestSchedulePreviewResponse)
async def preview_schedule(
    expression: str,
    timezone: str = "UTC",
    _current_user: User = Depends(get_current_user),
) -> TestSchedulePreviewResponse:
    """Preview the next 5 occurrences of a cron expression."""
    import zoneinfo
    from datetime import datetime
    from croniter import croniter

    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {timezone}")

    now = datetime.now(tz)
    try:
        if not croniter.is_valid(expression):
            raise ValueError("Invalid cron expression syntax")
        
        iter = croniter(expression, now)
        next_runs = []
        for _ in range(5):
            next_runs.append(iter.get_next(datetime))
        return TestSchedulePreviewResponse(next_runs=next_runs)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {str(e)}")


@router.post("/schedules", status_code=201, response_model=TestScheduleResponse)
async def create_schedule(
    req: TestScheduleCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Create and persist a new test schedule."""
    container = request.app.state.container
    import uuid
    import zoneinfo
    from datetime import UTC, datetime
    from croniter import croniter

    # Validate profile exists
    profile = await container.store.get_profile(req.profile_id)
    if not profile:
        raise HTTPException(status_code=400, detail=f"Profile {req.profile_id} not found")

    # Validate timezone
    try:
        tz = zoneinfo.ZoneInfo(req.timezone)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {req.timezone}")

    # Validate cron expression
    if not croniter.is_valid(req.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    # Compute static next_run_at preview
    next_run_at = None
    if req.enabled:
        try:
            iter = croniter(req.cron_expression, datetime.now(tz))
            next_run_at = iter.get_next(datetime)
        except Exception:
            pass

    schedule_id = str(uuid.uuid4())
    schedule = TestSchedule(
        id=schedule_id,
        name=req.name,
        profile_id=req.profile_id,
        cron_expression=req.cron_expression,
        enabled=req.enabled,
        timezone=req.timezone,
        last_run_at=None,
        next_run_at=next_run_at,
        created_by=current_user.username,
        created_at=datetime.now(UTC),
    )
    await container.store.save_schedule(schedule)

    # Register in in-process scheduler
    from qarunner.core.scheduler import add_or_update_schedule_job
    add_or_update_schedule_job(request.app, schedule)

    return schedule_to_response(schedule)


@router.get("/schedules", response_model=list[TestScheduleResponse])
async def list_schedules(
    request: Request,
    profile_id: str | None = None,
    _current_user: User = Depends(get_current_user),
) -> list[TestScheduleResponse]:
    """List all test schedules, optionally filtered by profile_id."""
    container = request.app.state.container
    schedules = await container.store.list_schedules(profile_id)
    return [schedule_to_response(s) for s in schedules]


@router.get("/schedules/{schedule_id}", response_model=TestScheduleResponse)
async def get_schedule(
    schedule_id: str,
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Retrieve a single test schedule by ID."""
    container = request.app.state.container
    schedule = await container.store.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return schedule_to_response(schedule)


@router.put("/schedules/{schedule_id}", response_model=TestScheduleResponse)
async def update_schedule(
    schedule_id: str,
    req: TestScheduleUpdateRequest,
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> TestScheduleResponse:
    """Update an existing test schedule."""
    container = request.app.state.container
    import zoneinfo
    from datetime import datetime
    from croniter import croniter

    existing = await container.store.get_schedule(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")

    # Validate profile exists
    profile = await container.store.get_profile(req.profile_id)
    if not profile:
        raise HTTPException(status_code=400, detail=f"Profile {req.profile_id} not found")

    # Validate timezone
    try:
        tz = zoneinfo.ZoneInfo(req.timezone)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {req.timezone}")

    # Validate cron expression
    if not croniter.is_valid(req.cron_expression):
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    # Compute static next_run_at preview
    next_run_at = None
    if req.enabled:
        try:
            iter = croniter(req.cron_expression, datetime.now(tz))
            next_run_at = iter.get_next(datetime)
        except Exception:
            pass

    updated = TestSchedule(
        id=existing.id,
        name=req.name,
        profile_id=req.profile_id,
        cron_expression=req.cron_expression,
        enabled=req.enabled,
        timezone=req.timezone,
        last_run_at=existing.last_run_at,
        next_run_at=next_run_at,
        created_by=existing.created_by,
        created_at=existing.created_at,
    )
    await container.store.save_schedule(updated)

    # Sync with in-process scheduler
    from qarunner.core.scheduler import add_or_update_schedule_job, remove_schedule_job
    if updated.enabled:
        add_or_update_schedule_job(request.app, updated)
    else:
        remove_schedule_job(request.app, updated.id)

    return schedule_to_response(updated)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    request: Request,
    _current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a test schedule."""
    container = request.app.state.container
    deleted = await container.store.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")

    # Remove from in-process scheduler
    from qarunner.core.scheduler import remove_schedule_job
    remove_schedule_job(request.app, schedule_id)

    return {"status": "success", "message": f"Schedule {schedule_id} deleted"}

