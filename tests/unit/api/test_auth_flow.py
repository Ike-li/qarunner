"""Unit and integration tests for the real authentication and user management flow."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.api.app import create_app
from qarunner.api.deps import create_container
from qarunner.config import Settings
from qarunner.core.auth import create_access_token

# The seeded admin uses QARUNNER_ADMIN_PASSWORD (set in conftest), so log in with
# that same value rather than the old hard-coded default (SEC-2).
ADMIN_PW = os.environ["QARUNNER_ADMIN_PASSWORD"]


@pytest.fixture
async def auth_app() -> FastAPI:
    """Create a real FastAPI application with an in-memory SqliteStore and no
    dependency overrides."""
    container = create_container(Settings(), store=SqliteStore(":memory:"))

    # Initialize the sqlite store
    await container.store.initialize()

    app = create_app(container)
    return app


@pytest.fixture
def client(auth_app: FastAPI) -> TestClient:
    return TestClient(auth_app)


def test_admin_login_success(client: TestClient) -> None:
    """Test successful login using the default pre-populated admin credentials."""
    resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # SEC-6: the token is also planted as an HttpOnly, SameSite=Strict cookie.
    set_cookie = resp.headers["set-cookie"].lower()
    assert "token=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie


def test_login_invalid_credentials(client: TestClient) -> None:
    """Test login failure with incorrect credentials."""
    resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert resp.status_code == 401
    assert "Incorrect username or password" in resp.json()["detail"]


def test_get_me_success_header(client: TestClient) -> None:
    """Test retrieving current user profile with valid Bearer token in headers."""
    # First login
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    token = login_resp.json()["access_token"]

    # Get /auth/me
    resp = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


def test_get_me_success_cookie(client: TestClient) -> None:
    """Login plants the HttpOnly cookie; a subsequent request with no
    Authorization header authenticates via that cookie (SEC-6)."""
    client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    # The TestClient jar now holds the cookie; send no Bearer header.
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_get_me_query_param_rejected(client: TestClient) -> None:
    """The ?token= URL fallback was removed (SEC-6): a token in the query string
    must NOT authenticate, even when it is otherwise valid."""
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    token = login_resp.json()["access_token"]

    # Drop the login cookie so we isolate the query-param path (otherwise the jar
    # would authenticate us via the cookie and mask the rejection).
    client.cookies.clear()
    resp = client.get(f"/auth/me?token={token}")
    assert resp.status_code == 401


def test_logout_clears_cookie(client: TestClient) -> None:
    """POST /auth/logout expires the cookie so the session no longer
    authenticates (SEC-6)."""
    client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    assert client.get("/auth/me").status_code == 200  # cookie works

    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 204

    # Cookie expired and dropped from the jar → no credentials left.
    assert client.get("/auth/me").status_code == 401


def test_get_me_missing_token(client: TestClient) -> None:
    """Test retrieving profile without any token raises 401."""
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_get_me_invalid_token(client: TestClient) -> None:
    """Test retrieving profile with malformed or invalid token raises 401."""
    resp = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalidtoken"},
    )
    assert resp.status_code == 401


def test_get_me_user_not_found_in_db(client: TestClient) -> None:
    """Test that a valid JWT with a username not present in the DB raises 401 User not found."""
    # Generate token for user "nonexistent"
    token = create_access_token("nonexistent", "user", Settings())
    resp = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "User not found" in resp.json()["detail"]


def test_user_creation_and_listing_by_admin(client: TestClient) -> None:
    """Test that an Admin user can successfully register and list new standard users."""
    # 1. Login as admin
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    admin_token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Register standard user "tester"
    create_resp = client.post(
        "/users",
        json={"username": "tester", "password": "testerpassword", "role": "user"},
        headers=headers,
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["username"] == "tester"
    assert data["role"] == "user"

    # 3. List users
    list_resp = client.get("/users", headers=headers)
    assert list_resp.status_code == 200
    users_list = list_resp.json()["users"]
    usernames = [u["username"] for u in users_list]
    assert "admin" in usernames
    assert "tester" in usernames


def test_user_creation_already_exists(client: TestClient) -> None:
    """Test that creating a user that already exists raises 400 Bad Request."""
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    admin_token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Register admin again
    resp = client.post(
        "/users",
        json={"username": "admin", "password": "newpassword", "role": "admin"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Username already exists" in resp.json()["detail"]


def test_non_admin_forbidden_actions(client: TestClient) -> None:
    """Test that a standard USER is forbidden from creating users, listing users,
    or accessing admin endpoints."""
    # 1. Login as admin to create a normal user "tester"
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    admin_token = login_resp.json()["access_token"]

    client.post(
        "/users",
        json={"username": "tester", "password": "testerpassword", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # 2. Login as the standard user "tester"
    tester_login = client.post(
        "/auth/login",
        json={"username": "tester", "password": "testerpassword"},
    )
    tester_token = tester_login.json()["access_token"]
    tester_headers = {"Authorization": f"Bearer {tester_token}"}

    # 3. Try to register a new user as "tester" (should return 403)
    create_resp = client.post(
        "/users",
        json={"username": "someuser", "password": "password", "role": "user"},
        headers=tester_headers,
    )
    assert create_resp.status_code == 403
    assert "Requires Admin role" in create_resp.json()["detail"]

    # 4. Try to list users as "tester" (should return 403)
    list_resp = client.get("/users", headers=tester_headers)
    assert list_resp.status_code == 403


# ── SEC-4 (extended): object-level authz for profiles & schedules ────────
# Profiles and schedules record their creator, yet only runs enforced ownership
# (SEC-4). A non-admin could list/read/modify/delete another user's profile or
# schedule (IDOR). These real two-user flows reproduce that and turn red if the
# ownership checks in the profile/schedule routes are removed.


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    """Login *username* and return a Bearer auth header.

    Clears the cookie jar so each request authenticates only via the returned
    token — otherwise the login cookie would silently carry the last user's
    identity across requests and mask an authz regression.
    """
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


_USER_PW = "Str0ng-User-Pw!2026"


@pytest.fixture
def two_users(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    """Create two standard users (alice, bob); return their auth headers."""
    admin = _login(client, "admin", ADMIN_PW)
    for name in ("alice", "bob"):
        resp = client.post(
            "/users",
            json={"username": name, "password": _USER_PW, "role": "user"},
            headers=admin,
        )
        assert resp.status_code == 201, resp.text
    return _login(client, "alice", _USER_PW), _login(client, "bob", _USER_PW)


def _create_profile(client: TestClient, headers: dict[str, str], name: str) -> str:
    resp = client.post(
        "/profiles",
        json={
            "name": name,
            "tests_path": "suite_demo",
            "selected_files": [],
            "selected_markers": [],
            "extra_args": "",
            "executor_mode": "subprocess",
            "env": {},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


_PROFILE_UPDATE = {
    "name": "renamed",
    "tests_path": "suite_demo",
    "selected_files": [],
    "selected_markers": [],
    "extra_args": "",
    "executor_mode": "subprocess",
    "env": {},
}


def test_profile_idor_blocked_for_non_owner(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    """A non-owner can neither see, modify nor delete another user's profile."""
    alice, bob = two_users
    pid = _create_profile(client, alice, "alice-profile")

    # bob's list must not leak alice's profile; alice's own list still has it.
    assert all(p["id"] != pid for p in client.get("/profiles", headers=bob).json())
    assert any(p["id"] == pid for p in client.get("/profiles", headers=alice).json())

    # bob cannot tamper with or delete it.
    assert client.put(f"/profiles/{pid}", json=_PROFILE_UPDATE, headers=bob).status_code == 403
    assert client.delete(f"/profiles/{pid}", headers=bob).status_code == 403

    # alice's profile survived bob's attempts.
    assert any(p["id"] == pid for p in client.get("/profiles", headers=alice).json())


def test_profile_access_allowed_for_owner_and_admin(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    """The owner and any admin retain full access to a profile."""
    alice, _bob = two_users
    pid = _create_profile(client, alice, "alice-profile")

    # Owner can update.
    assert client.put(f"/profiles/{pid}", json=_PROFILE_UPDATE, headers=alice).status_code == 200

    # Admin sees every profile and can delete anyone's.
    admin = _login(client, "admin", ADMIN_PW)
    assert any(p["id"] == pid for p in client.get("/profiles", headers=admin).json())
    assert client.delete(f"/profiles/{pid}", headers=admin).status_code == 200


def _create_schedule(client: TestClient, headers: dict[str, str], profile_id: str) -> str:
    resp = client.post(
        "/schedules",
        json={
            "name": "sched",
            "profile_id": profile_id,
            "cron_expression": "0 3 * * *",
            "enabled": True,
            "timezone": "UTC",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


_SCHEDULE_UPDATE = {
    "name": "renamed",
    "profile_id": "",  # filled per-test with a real profile id
    "cron_expression": "0 4 * * *",
    "enabled": False,
    "timezone": "UTC",
}


def test_schedule_idor_blocked_for_non_owner(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    """A non-owner can neither see, modify nor delete another user's schedule."""
    alice, bob = two_users
    pid = _create_profile(client, alice, "alice-profile")
    sid = _create_schedule(client, alice, pid)

    # bob's list/get must not expose alice's schedule.
    assert all(s["id"] != sid for s in client.get("/schedules", headers=bob).json())
    assert client.get(f"/schedules/{sid}", headers=bob).status_code == 403

    # bob cannot tamper with or delete it.
    upd = {**_SCHEDULE_UPDATE, "profile_id": pid}
    assert client.put(f"/schedules/{sid}", json=upd, headers=bob).status_code == 403
    assert client.delete(f"/schedules/{sid}", headers=bob).status_code == 403

    # alice still owns an intact schedule.
    assert client.get(f"/schedules/{sid}", headers=alice).status_code == 200


def test_schedule_access_allowed_for_owner_and_admin(
    client: TestClient, two_users: tuple[dict[str, str], dict[str, str]]
) -> None:
    """The owner and any admin retain full access to a schedule."""
    alice, _bob = two_users
    pid = _create_profile(client, alice, "alice-profile")
    sid = _create_schedule(client, alice, pid)

    # Owner can read it back.
    assert client.get(f"/schedules/{sid}", headers=alice).status_code == 200

    # Admin sees every schedule and can delete anyone's.
    admin = _login(client, "admin", ADMIN_PW)
    assert any(s["id"] == sid for s in client.get("/schedules", headers=admin).json())
    assert client.delete(f"/schedules/{sid}", headers=admin).status_code == 200
