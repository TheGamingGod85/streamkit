from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
from uuid import UUID

import pytest
from PIL import Image

from tests.conftest import FakeQueuePublisher, FakeR2Service, FakeSupabaseService


def _build_image_bytes(format_name: str = "PNG") -> bytes:
    image = Image.new("RGB", (12, 8), color=(24, 72, 120))
    buffer = io.BytesIO()
    image.save(buffer, format=format_name)
    return buffer.getvalue()


def _encode_segment(data: dict[str, object]) -> str:
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _build_supabase_jwt(secret: str, subject: str, *, expires_in_seconds: int = 3600) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": "authenticated",
        "iat": now,
        "nbf": now,
        "exp": now + expires_in_seconds,
    }
    header_segment = _encode_segment(header)
    payload_segment = _encode_segment(payload)
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_segment = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{header_segment}.{payload_segment}.{signature_segment}"


@pytest.mark.asyncio
async def test_private_media_requires_owner_token(
    client,
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
    monkeypatch,
) -> None:
    owner_id = "11111111-1111-1111-1111-111111111111"
    other_user_id = "22222222-2222-2222-2222-222222222222"
    owner_token = _build_supabase_jwt("test-jwt-secret", owner_id)
    other_token = _build_supabase_jwt("test-jwt-secret", other_user_id)

    monkeypatch.setattr("worker.tasks.image_task.enqueue_image_processing", lambda *args, **kwargs: "task-private")

    upload_response = await client.post(
        "/upload",
        headers={"Authorization": f"Bearer {owner_token}"},
        files={"file": ("private.png", _build_image_bytes(), "image/png")},
    )

    assert upload_response.status_code == 201
    asset_id = upload_response.json()["data"]["asset_id"]
    asset_row = fake_supabase_service.assets[asset_id]
    asset_row["status"] = "ready"
    asset_row["thumbnail_url"] = "https://cdn.example.test/private-thumb.webp"
    asset_row.setdefault("metadata", {})["image"] = {
        "preview_webp_url": "https://cdn.example.test/private-preview.webp",
        "smart_thumbnail_url": "https://cdn.example.test/private-thumb.webp",
    }

    assert asset_row["user_id"] == owner_id

    private_asset_response = await client.get(f"/assets/{asset_id}", headers={"Authorization": f"Bearer {owner_token}"})
    assert private_asset_response.status_code == 200
    private_asset_body = private_asset_response.json()["data"]
    assert private_asset_body["asset"]["user_id"] == owner_id
    assert private_asset_body["playback"]["can_play"] is True

    transform_response = await client.get(
        f"/img/{asset_id}?w=4&h=4&f=webp&crop=center",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert transform_response.status_code == 200
    assert transform_response.headers["content-type"] == "image/webp"

    denied_asset_response = await client.get(f"/assets/{asset_id}", headers={"Authorization": f"Bearer {other_token}"})
    assert denied_asset_response.status_code == 403

    denied_player_response = await client.get(f"/player/{asset_id}", headers={"Authorization": f"Bearer {other_token}"})
    assert denied_player_response.status_code == 403

    denied_transform_response = await client.get(
        f"/img/{asset_id}?w=4&h=4&f=webp&crop=center",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied_transform_response.status_code == 403


@pytest.mark.asyncio
async def test_public_media_routes_work_without_auth(
    client,
    fake_r2_service: FakeR2Service,
    fake_supabase_service: FakeSupabaseService,
    fake_queue_publisher: FakeQueuePublisher,
    monkeypatch,
) -> None:
    monkeypatch.setattr("worker.tasks.image_task.enqueue_image_processing", lambda *args, **kwargs: "task-public")

    upload_response = await client.post(
        "/upload",
        files={"file": ("public.png", _build_image_bytes(), "image/png")},
    )

    assert upload_response.status_code == 201
    asset_id = upload_response.json()["data"]["asset_id"]
    asset_row = fake_supabase_service.assets[asset_id]
    asset_row["status"] = "ready"
    asset_row["thumbnail_url"] = "https://cdn.example.test/public-thumb.webp"
    asset_row.setdefault("metadata", {})["image"] = {
        "preview_webp_url": "https://cdn.example.test/public-preview.webp",
        "smart_thumbnail_url": "https://cdn.example.test/public-thumb.webp",
    }

    asset_response = await client.get(f"/assets/{asset_id}")
    assert asset_response.status_code == 200
    asset_body = asset_response.json()["data"]
    assert asset_body["asset"]["id"] == asset_id
    assert asset_body["playback"]["can_play"] is True

    player_response = await client.get(f"/player/{asset_id}")
    assert player_response.status_code == 200
    assert "StreamKit Player" in player_response.text
    assert "public-preview.webp" in player_response.text