from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api.core.config import Settings
from api.main import create_app


@dataclass
class FakeR2Service:
    uploaded: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    objects: dict[str, bytes] = field(default_factory=dict)

    async def upload_file(
        self,
        *,
        fileobj: Any,
        object_key: str,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        data = fileobj.read()
        self.objects[object_key] = data
        self.uploaded.append(
            {
                "object_key": object_key,
                "content_type": content_type,
                "metadata": dict(metadata or {}),
                "size_bytes": len(data),
            }
        )
        return f"https://cdn.example.test/{object_key}"

    async def delete_file(self, object_key: str) -> None:
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    async def download_bytes(self, object_key: str) -> bytes:
        return self.objects[object_key]


@dataclass
class FakeSupabaseService:
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_assets: list[dict[str, Any]] = field(default_factory=list)
    created_jobs: list[dict[str, Any]] = field(default_factory=list)

    async def create_asset(self, asset_data: dict[str, Any]) -> dict[str, Any]:
        row = self._stamp(asset_data)
        self.assets[str(row["id"])] = row
        self.created_assets.append(row)
        return row

    async def create_job(self, job_data: dict[str, Any]) -> dict[str, Any]:
        row = self._stamp(job_data)
        self.jobs[str(row["id"])] = row
        self.created_jobs.append(row)
        return row

    async def update_asset_status(
        self,
        asset_id: UUID | str,
        status: str,
        *,
        metadata: dict[str, Any] | None = None,
        master_url: str | None = None,
        thumbnail_url: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        row = self.assets[str(asset_id)]
        row["status"] = status
        row["updated_at"] = "2026-04-10T00:00:00Z"
        if metadata is not None:
            row["metadata"] = dict(metadata)
        if master_url is not None:
            row["master_url"] = master_url
        if thumbnail_url is not None:
            row["thumbnail_url"] = thumbnail_url
        if error is not None:
            row.setdefault("metadata", {})["error"] = error
        return row

    async def update_job_status(
        self,
        job_id: UUID | str,
        status: str,
        *,
        progress: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        row = self.jobs[str(job_id)]
        row["status"] = status
        row["updated_at"] = "2026-04-10T00:00:00Z"
        if progress is not None:
            row["progress"] = progress
        if error is not None:
            row["error"] = error
        return row

    async def get_asset(self, asset_id: UUID | str) -> dict[str, Any] | None:
        return self.assets.get(str(asset_id))

    async def list_assets(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        rows = sorted(self.assets.values(), key=lambda row: row.get("created_at", ""), reverse=True)
        return [dict(row) for row in rows[offset : offset + max(1, limit)]]

    async def list_jobs(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        rows = sorted(self.jobs.values(), key=lambda row: row.get("created_at", ""), reverse=True)
        return [dict(row) for row in rows[offset : offset + max(1, limit)]]

    async def list_jobs_for_asset(self, asset_id: UUID | str, limit: int = 10) -> list[dict[str, Any]]:
        rows = [row for row in self.jobs.values() if str(row.get("asset_id")) == str(asset_id)]
        rows = sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)
        return [dict(row) for row in rows[: max(1, limit)]]

    async def get_job(self, job_id: UUID | str) -> dict[str, Any] | None:
        return self.jobs.get(str(job_id))

    @staticmethod
    def _stamp(data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        row.setdefault("created_at", "2026-04-10T00:00:00Z")
        row.setdefault("updated_at", "2026-04-10T00:00:00Z")
        row.setdefault("metadata", {})
        return row


@dataclass
class FakeQueuePublisher:
    published: list[dict[str, Any]] = field(default_factory=list)

    async def publish_job(
        self,
        *,
        asset_id: UUID | str,
        job_id: UUID | str,
        job_type: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        record = {
            "asset_id": str(asset_id),
            "job_id": str(job_id),
            "job_type": job_type,
            "payload": dict(payload or {}),
        }
        self.published.append(record)
        return "1-0"


@pytest.fixture
def fake_r2_service() -> FakeR2Service:
    return FakeR2Service()


@pytest.fixture
def fake_supabase_service() -> FakeSupabaseService:
    return FakeSupabaseService()


@pytest.fixture
def fake_queue_publisher() -> FakeQueuePublisher:
    return FakeQueuePublisher()


@pytest.fixture
def app(
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
) -> FastAPI:
    settings = Settings(
        app_env="test",
        secret_key="test-secret",
        max_upload_size_mb=5,
        allowed_origins=["*"],
        r2_account_id="test-account",
        r2_access_key_id="test-key",
        r2_secret_access_key="test-secret-key",
        r2_bucket_name="test-bucket",
        r2_public_url="https://pub.test.example",
        supabase_url="https://supabase.test.example",
        supabase_anon_key="test-anon",
        supabase_service_role_key="test-service-role",
        supabase_jwt_secret="test-jwt-secret",
        supabase_management_api_key="test-mgmt-key",
        cloudflare_api_token="test-cloudflare-token",
        cloudflare_account_id="test-cloudflare-account",
        redis_url="redis://redis:6379/0",
    )
    return create_app(
        settings=settings,
        r2_service=fake_r2_service,
        supabase_service=fake_supabase_service,
        queue_publisher=fake_queue_publisher,
    )


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
