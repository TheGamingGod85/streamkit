from __future__ import annotations

import io
from uuid import UUID

import pytest
from PIL import Image

from tests.conftest import FakeQueuePublisher, FakeR2Service, FakeSupabaseService


def _build_image_bytes(format_name: str = "PNG") -> bytes:
	image = Image.new("RGB", (8, 6), color=(12, 34, 56))
	buffer = io.BytesIO()
	image.save(buffer, format=format_name)
	return buffer.getvalue()


@pytest.mark.asyncio
async def test_image_upload_enqueues_worker_pipeline(
	client,
	fake_r2_service: FakeR2Service,
	fake_supabase_service: FakeSupabaseService,
	fake_queue_publisher: FakeQueuePublisher,
	monkeypatch,
) -> None:
	calls: list[dict[str, str]] = []

	def fake_enqueue(asset_id, job_id, source_object_key):
		calls.append(
			{
				"asset_id": str(asset_id),
				"job_id": str(job_id),
				"source_object_key": source_object_key,
			}
		)
		return "task-456"

	monkeypatch.setattr("worker.tasks.image_task.enqueue_image_processing", fake_enqueue)

	response = await client.post(
		"/upload",
		files={"file": ("photo.png", _build_image_bytes("PNG"), "image/png")},
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


@pytest.mark.asyncio
async def test_transform_route_returns_resized_image(
	client,
	fake_r2_service: FakeR2Service,
	fake_supabase_service: FakeSupabaseService,
	monkeypatch,
) -> None:
	monkeypatch.setattr("worker.tasks.image_task.enqueue_image_processing", lambda *args, **kwargs: "task-789")

	response = await client.post(
		"/upload",
		files={"file": ("photo.png", _build_image_bytes("PNG"), "image/png")},
	)

	assert response.status_code == 201
	body = response.json()["data"]
	asset_id = body["asset_id"]

	transform_response = await client.get(f"/img/{asset_id}?w=4&h=4&f=webp&crop=center")

	assert transform_response.status_code == 200
	assert transform_response.headers["content-type"] == "image/webp"
	assert transform_response.headers["x-image-width"] == "4"
	assert transform_response.headers["x-image-height"] == "4"

	transformed_image = Image.open(io.BytesIO(transform_response.content))
	assert transformed_image.size == (4, 4)
	assert transformed_image.format == "WEBP"

	asset_row = fake_supabase_service.assets[str(UUID(asset_id))]
	assert asset_row["type"] == "image"
