"""Unit and integration tests for the real authentication and user management flow."""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

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
    settings = Settings()
    # Use in-memory SQLite for testing real database interactions
    settings.db_path = ":memory:"
    container = create_container(settings)

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


def test_get_me_success_query_param(client: TestClient) -> None:
    """Test retrieving the current user profile via the query-parameter token
    fallback (e.g. for Allure reports)."""
    login_resp = client.post(
        "/auth/login",
        json={"username": "admin", "password": ADMIN_PW},
    )
    token = login_resp.json()["access_token"]

    # Get /auth/me with query param ?token=...
    resp = client.get(f"/auth/me?token={token}")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


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
    token = create_access_token("nonexistent", "user")
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
