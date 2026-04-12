from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from api.core.config import Settings
from streamkit_mcp.context import StreamKitMCPContext
from streamkit_mcp.tools._shared import StreamKitMCPError
from streamkit_mcp.tools.media_tools import get_media_links
from streamkit_mcp.tools.query_tools import get_asset_details, list_asset_jobs, list_recent_assets
from streamkit_mcp.tools.setup_tools import get_server_summary

from tests.conftest import FakeSupabaseService


class FakeMCPContext:
	def __init__(self, app_context: StreamKitMCPContext) -> None:
		self.request_context = SimpleNamespace(lifespan_context=app_context)


def _build_app_context(fake_supabase_service: FakeSupabaseService) -> StreamKitMCPContext:
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

	class FakeR2Service:
		pass

	return StreamKitMCPContext(
		settings=settings,
		r2_service=FakeR2Service(),
		supabase_service=fake_supabase_service,
	)


def _seed_public_asset(fake_supabase_service: FakeSupabaseService, *, asset_id: str, job_id: str) -> None:
	fake_supabase_service.assets[asset_id] = {
		"id": asset_id,
		"user_id": None,
		"type": "image",
		"status": "ready",
		"original_filename": "public.png",
		"r2_base_path": f"assets/{asset_id}",
		"master_url": None,
		"thumbnail_url": "https://cdn.example.test/public-thumb.webp",
		"metadata": {
			"content_type": "image/png",
			"image": {
				"preview_webp_url": "https://cdn.example.test/public-preview.webp",
				"smart_thumbnail_url": "https://cdn.example.test/public-thumb.webp",
			},
		},
		"created_at": "2026-04-10T00:00:00Z",
		"updated_at": "2026-04-10T00:00:00Z",
	}
	fake_supabase_service.jobs[job_id] = {
		"id": job_id,
		"asset_id": asset_id,
		"type": "upload",
		"status": "ready",
		"progress": 100,
		"error": None,
		"created_at": "2026-04-10T00:00:00Z",
		"updated_at": "2026-04-10T00:00:00Z",
	}


def _seed_private_asset(fake_supabase_service: FakeSupabaseService, *, asset_id: str) -> None:
	fake_supabase_service.assets[asset_id] = {
		"id": asset_id,
		"user_id": "11111111-1111-1111-1111-111111111111",
		"type": "video",
		"status": "ready",
		"original_filename": "private.mp4",
		"r2_base_path": f"assets/{asset_id}",
		"master_url": "https://cdn.example.test/private/master.m3u8",
		"thumbnail_url": "https://cdn.example.test/private-thumb.jpg",
		"metadata": {},
		"created_at": "2026-04-10T00:00:00Z",
		"updated_at": "2026-04-10T00:00:00Z",
	}


@pytest.mark.asyncio
async def test_server_summary_reflects_public_only_stack(fake_supabase_service: FakeSupabaseService) -> None:
	app_context = _build_app_context(fake_supabase_service)
	summary = await get_server_summary(FakeMCPContext(app_context))

	assert summary.server_name == "StreamKit"
	assert summary.public_only is True
	assert summary.transport == "streamable-http"
	assert summary.routes["image_transform"] == "/img/{asset_id}"
	assert summary.services["supabase"] is True


@pytest.mark.asyncio
async def test_list_recent_assets_filters_private_rows(fake_supabase_service: FakeSupabaseService) -> None:
	public_asset_id = "11111111-1111-1111-1111-111111111111"
	private_asset_id = "22222222-2222-2222-2222-222222222222"
	_seed_public_asset(fake_supabase_service, asset_id=public_asset_id, job_id="33333333-3333-3333-3333-333333333333")
	_seed_private_asset(fake_supabase_service, asset_id=private_asset_id)

	app_context = _build_app_context(fake_supabase_service)
	result = await list_recent_assets(FakeMCPContext(app_context), limit=10)

	assert result.count == 1
	assert result.items[0].id == UUID(public_asset_id)


@pytest.mark.asyncio
async def test_asset_details_and_media_links_for_public_asset(fake_supabase_service: FakeSupabaseService) -> None:
	asset_id = "11111111-1111-1111-1111-111111111111"
	job_id = "33333333-3333-3333-3333-333333333333"
	_seed_public_asset(fake_supabase_service, asset_id=asset_id, job_id=job_id)

	app_context = _build_app_context(fake_supabase_service)
	details = await get_asset_details(FakeMCPContext(app_context), asset_id=asset_id)
	media_links = await get_media_links(
		FakeMCPContext(app_context),
		asset_id=asset_id,
		width=640,
		height=640,
		format="webp",
		crop="smart",
	)
	jobs = await list_asset_jobs(FakeMCPContext(app_context), asset_id=asset_id)

	assert details.asset.id == UUID(asset_id)
	assert details.playback["kind"] == "image"
	assert details.player_url == f"/player/{asset_id}"
	assert len(details.jobs) == 1
	assert media_links.image_transform_url == f"/img/{asset_id}?w=640&h=640&f=webp&q=80&crop=smart"
	assert media_links.preview_url == "https://cdn.example.test/public-preview.webp"
	assert jobs.count == 1
	assert jobs.items[0].id == UUID(job_id)


@pytest.mark.asyncio
async def test_private_asset_is_hidden_from_mcp(fake_supabase_service: FakeSupabaseService) -> None:
	asset_id = "22222222-2222-2222-2222-222222222222"
	_seed_private_asset(fake_supabase_service, asset_id=asset_id)

	app_context = _build_app_context(fake_supabase_service)

	with pytest.raises(StreamKitMCPError):
		await get_asset_details(FakeMCPContext(app_context), asset_id=asset_id)
