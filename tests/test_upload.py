from __future__ import annotations

from uuid import UUID

import pytest

from tests.conftest import FakeQueuePublisher, FakeR2Service, FakeSupabaseService


@pytest.mark.asyncio
async def test_upload_creates_asset_and_job(
    client,
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
    monkeypatch,
) -> None:
    monkeypatch.setattr("worker.tasks.image_task.enqueue_image_processing", lambda *args, **kwargs: "task-123")

    response = await client.post(
        "/upload",
        files={"file": ("photo.jpg", b"\xff\xd8\xff" + b"0" * 1024, "image/jpeg")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    asset_id = data["asset_id"]
    job_id = data["job_id"]

    assert UUID(asset_id)
    assert UUID(job_id)
    assert data["status_url"] == f"/status/{asset_id}"

    assert len(fake_r2_service.uploaded) == 1
    assert fake_r2_service.uploaded[0]["content_type"] == "image/jpeg"
    assert fake_r2_service.uploaded[0]["object_key"].startswith(f"assets/{asset_id}/")
    assert fake_r2_service.uploaded[0]["size_bytes"] > 0

    assert len(fake_supabase_service.created_assets) == 1
    assert len(fake_supabase_service.created_jobs) == 1
    assert fake_supabase_service.created_assets[0]["id"] == asset_id
    assert fake_supabase_service.created_jobs[0]["id"] == job_id
    assert len(fake_queue_publisher.published) == 1
    assert fake_queue_publisher.published[0]["asset_id"] == asset_id
    assert fake_queue_publisher.published[0]["job_id"] == job_id
    assert fake_queue_publisher.published[0]["job_type"] == "upload"
    assert fake_queue_publisher.published[0]["payload"]["object_key"].startswith(f"assets/{asset_id}/")

    status_response = await client.get(f"/status/{asset_id}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["success"] is True
    assert status_body["data"]["status"] == "queued"
    assert status_body["data"]["asset"]["id"] == asset_id


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime(client) -> None:
    response = await client.post(
        "/upload",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 415
    body = response.json()
    assert body["success"] is False
    assert "Only image and video uploads are supported" in body["error"]


@pytest.mark.asyncio
async def test_upload_rejects_files_over_limit(
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
) -> None:
    from api.core.config import Settings
    from api.main import create_app
    import httpx
    from fastapi import FastAPI

    settings = Settings(
        app_env="test",
        secret_key="test-secret",
        max_upload_size_mb=1,
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
    app: FastAPI = create_app(settings=settings, r2_service=fake_r2_service, supabase_service=fake_supabase_service)
    app.state.queue_publisher = fake_queue_publisher
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as oversized_client:
        response = await oversized_client.post(
            "/upload",
            files={"file": ("video.mp4", b"0" * (settings.max_upload_size_bytes + 1), "video/mp4")},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["success"] is False
    assert "maximum size" in body["error"]
    assert len(fake_r2_service.uploaded) == 0
    assert len(fake_supabase_service.created_assets) == 0


@pytest.mark.asyncio
async def test_video_upload_enqueues_worker_pipeline(
    client,
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
    monkeypatch,
) -> None:
    calls: list[dict[str, str]] = []

    def fake_enqueue(asset_id, job_id, source_object_key, qualities=None):
        calls.append(
            {
                "asset_id": str(asset_id),
                "job_id": str(job_id),
                "source_object_key": source_object_key,
            }
        )
        return "task-123"

    monkeypatch.setattr("worker.tasks.video_task.enqueue_video_processing", fake_enqueue)

    response = await client.post(
        "/upload",
        files={"file": ("clip.mp4", b"\x00" * 4096, "video/mp4")},
    )

    assert response.status_code == 201
    body = response.json()
    asset_id = body["data"]["asset_id"]
    job_id = body["data"]["job_id"]

    assert len(fake_r2_service.uploaded) == 1
    assert len(fake_supabase_service.created_assets) == 1
    assert len(fake_supabase_service.created_jobs) == 1
    assert len(fake_queue_publisher.published) == 1
    assert len(calls) == 1
    assert calls[0]["asset_id"] == asset_id
    assert calls[0]["job_id"] == job_id
    assert calls[0]["source_object_key"] == fake_queue_publisher.published[0]["payload"]["object_key"]
