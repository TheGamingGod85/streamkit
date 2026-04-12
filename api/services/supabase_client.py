from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from postgrest import APIError
from supabase import AsyncClient, create_async_client

from api.core.config import Settings


class SupabaseServiceError(RuntimeError):
    """Raised when a Supabase operation fails after retries."""


class SupabaseRepository:
    """Async repository wrapper around the Supabase Python client."""

    def __init__(self, client: AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def client(self) -> AsyncClient:
        """Backward-compatible access to the underlying Supabase async client."""

        return self._client

    @classmethod
    async def create(cls, settings: Settings) -> "SupabaseRepository":
        client = await create_async_client(settings.supabase_url, settings.supabase_service_role_key)
        return cls(client=client, settings=settings)

    async def create_asset(self, asset_data: Mapping[str, Any]) -> dict[str, Any]:
        """Insert an asset row and return the created record."""

        response = await self._execute_with_retries(
            lambda: self._client.table("assets").insert(dict(asset_data)).execute(),
            action="create asset",
        )
        return self._unwrap_single_row(response.data, "asset")

    async def create_job(self, job_data: Mapping[str, Any]) -> dict[str, Any]:
        """Insert a job row and return the created record."""

        response = await self._execute_with_retries(
            lambda: self._client.table("jobs").insert(dict(job_data)).execute(),
            action="create job",
        )
        return self._unwrap_single_row(response.data, "job")

    async def list_assets(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        """Return recent asset rows ordered by newest first."""

        response = await self._execute_with_retries(
            lambda: self._client.table("assets").select("*").order("created_at", desc=True).limit(
                max(1, limit + offset)
            ).execute(),
            action="list assets",
        )
        rows = [dict(row) for row in (response.data or [])]
        return rows[offset : offset + max(1, limit)]

    async def list_jobs(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        """Return recent job rows ordered by newest first."""

        response = await self._execute_with_retries(
            lambda: self._client.table("jobs").select("*").order("created_at", desc=True).limit(
                max(1, limit + offset)
            ).execute(),
            action="list jobs",
        )
        rows = [dict(row) for row in (response.data or [])]
        return rows[offset : offset + max(1, limit)]

    async def list_jobs_for_asset(self, asset_id: UUID | str, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent job rows for a given asset."""

        response = await self._execute_with_retries(
            lambda: self._client.table("jobs").select("*").eq("asset_id", str(asset_id)).order(
                "created_at", desc=True
            ).limit(max(1, limit)).execute(),
            action="list jobs for asset",
        )
        return [dict(row) for row in (response.data or [])]

    async def get_job(self, job_id: UUID | str) -> dict[str, Any] | None:
        """Fetch one job by ID."""

        response = await self._execute_with_retries(
            lambda: self._client.table("jobs").select("*").eq("id", str(job_id)).limit(1).execute(),
            action="fetch job",
        )
        rows = response.data or []
        if not rows:
            return None
        return dict(rows[0])

    async def update_asset_status(
        self,
        asset_id: UUID | str,
        status: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        master_url: str | None = None,
        thumbnail_url: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update asset status and any related fields."""

        payload: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        if master_url is not None:
            payload["master_url"] = master_url
        if thumbnail_url is not None:
            payload["thumbnail_url"] = thumbnail_url
        if error is not None:
            payload["metadata"] = {**dict(metadata or {}), "error": error}

        response = await self._execute_with_retries(
            lambda: self._client.table("assets").update(payload).eq("id", str(asset_id)).execute(),
            action="update asset",
        )
        return self._unwrap_single_row(response.data, "asset")

    async def update_job_status(
        self,
        job_id: UUID | str,
        status: str,
        *,
        progress: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update job status and progress."""

        payload: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if progress is not None:
            payload["progress"] = progress
        if error is not None:
            payload["error"] = error

        response = await self._execute_with_retries(
            lambda: self._client.table("jobs").update(payload).eq("id", str(job_id)).execute(),
            action="update job",
        )
        return self._unwrap_single_row(response.data, "job")

    async def get_asset(self, asset_id: UUID | str) -> dict[str, Any] | None:
        """Fetch one asset by ID."""

        response = await self._execute_with_retries(
            lambda: self._client.table("assets").select("*").eq("id", str(asset_id)).limit(1).execute(),
            action="fetch asset",
        )
        rows = response.data or []
        if not rows:
            return None
        return dict(rows[0])

    async def aclose(self) -> None:
        """Close the underlying Supabase client if it exposes an async close method."""

        closer = getattr(self._client, "aclose", None)
        if closer is None:
            return
        result = closer()
        if asyncio.iscoroutine(result):
            await result

    async def _execute_with_retries(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        action: str,
    ) -> Any:
        last_error: APIError | None = None
        for attempt in range(1, 4):
            try:
                return await operation()
            except APIError as exc:
                last_error = exc
                if attempt == 3:
                    raise SupabaseServiceError(self._format_api_error(action, exc)) from exc
                await asyncio.sleep(0.25 * attempt)
        if last_error is not None:
            raise SupabaseServiceError(self._format_api_error(action, last_error)) from last_error
        raise SupabaseServiceError(f"Supabase {action} failed unexpectedly.")

    @staticmethod
    def _unwrap_single_row(data: Any, entity_name: str) -> dict[str, Any]:
        if isinstance(data, list) and data:
            return dict(data[0])
        if isinstance(data, dict):
            return dict(data)
        raise SupabaseServiceError(f"Supabase did not return a {entity_name} row.")

    @staticmethod
    def _format_api_error(action: str, exc: APIError) -> str:
        message = getattr(exc, "message", str(exc))
        code = getattr(exc, "code", None)
        hint = getattr(exc, "hint", None)
        details = getattr(exc, "details", None)
        pieces = [f"Supabase {action} failed: {message}"]
        if code:
            pieces.append(f"code={code}")
        if hint:
            pieces.append(f"hint={hint}")
        if details:
            pieces.append(f"details={details}")
        return " | ".join(pieces)
