"""Application service for the execution-profile lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from qarunner.errors import ProfileNotFound
from qarunner.models import TestProfile

if TYPE_CHECKING:
    from qarunner.api.schemas import TestProfileCreateRequest, TestProfileUpdateRequest
    from qarunner.ports.store import RunStore


class ProfileService:
    """Builds and persists execution profiles.

    Keeps the HTTP layer a thin translate-call-return shell and raises domain
    errors (mapped to HTTP by the route) rather than HTTPException.
    """

    def __init__(self, store: RunStore) -> None:
        self._store = store

    async def create(self, req: TestProfileCreateRequest, created_by: str) -> TestProfile:
        profile = TestProfile(
            id=str(uuid4()),
            name=req.name,
            description=req.description,
            tests_path=req.tests_path,
            runner=req.runner,
            selected_files=req.selected_files,
            selected_markers=req.selected_markers,
            extra_args=req.extra_args,
            executor_mode="docker",
            timeout=req.timeout,
            created_by=created_by,
            created_at=datetime.now(UTC),
            env=req.env,
            webhook_url=req.webhook_url,
        )
        await self._store.save_profile(profile)
        return profile

    async def update(self, profile_id: str, req: TestProfileUpdateRequest) -> TestProfile:
        existing = await self._store.get_profile(profile_id)
        if not existing:
            raise ProfileNotFound(profile_id)
        updated = TestProfile(
            id=existing.id,
            name=req.name,
            description=req.description,
            tests_path=req.tests_path,
            runner=req.runner,
            selected_files=req.selected_files,
            selected_markers=req.selected_markers,
            extra_args=req.extra_args,
            executor_mode="docker",
            timeout=req.timeout,
            created_by=existing.created_by,
            created_at=existing.created_at,
            env=req.env,
            webhook_url=req.webhook_url,
        )
        await self._store.save_profile(updated)
        return updated

    async def delete(self, profile_id: str) -> None:
        if not await self._store.delete_profile(profile_id):
            raise ProfileNotFound(profile_id)
