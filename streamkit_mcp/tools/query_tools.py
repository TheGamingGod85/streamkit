from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from api.models.asset import Asset, Job

from streamkit_mcp.context import StreamKitMCPContext
from streamkit_mcp.schemas import AssetCollection, AssetDetails, JobCollection
from streamkit_mcp.tools._shared import build_playback_payload, get_app_context, require_public_asset


async def list_recent_assets(
    ctx: Context[ServerSession, StreamKitMCPContext],
    limit: int = 10,
) -> AssetCollection:
    """Return the newest public assets tracked by StreamKit."""

    app_context = get_app_context(ctx)
    rows = await app_context.supabase_service.list_assets(limit=max(1, min(limit * 5, 100)))
    assets = [Asset.model_validate(row) for row in rows if row.get("user_id") in (None, "")]
    assets = assets[: max(1, limit)]
    return AssetCollection(items=assets, count=len(assets), limit=max(1, limit))


async def get_asset_details(
    ctx: Context[ServerSession, StreamKitMCPContext],
    asset_id: str,
    job_limit: int = 10,
) -> AssetDetails:
    """Return one public asset with playback metadata and recent jobs."""

    app_context = get_app_context(ctx)
    asset_row = await app_context.supabase_service.get_asset(asset_id)
    if asset_row is None:
        raise ValueError(f"Asset '{asset_id}' was not found.")
    asset = require_public_asset(asset_row, asset_id)
    playback = build_playback_payload(asset)
    job_rows = await app_context.supabase_service.list_jobs_for_asset(asset_id, limit=max(1, min(job_limit, 50)))
    jobs = [Job.model_validate(row) for row in job_rows]
    return AssetDetails(asset=asset, playback=playback, player_url=f"/player/{asset.id}", jobs=jobs)


async def list_asset_jobs(
    ctx: Context[ServerSession, StreamKitMCPContext],
    asset_id: str,
    limit: int = 10,
) -> JobCollection:
    """Return the newest jobs for a public asset."""

    app_context = get_app_context(ctx)
    asset_row = await app_context.supabase_service.get_asset(asset_id)
    if asset_row is None:
        raise ValueError(f"Asset '{asset_id}' was not found.")
    require_public_asset(asset_row, asset_id)
    job_rows = await app_context.supabase_service.list_jobs_for_asset(asset_id, limit=max(1, min(limit, 50)))
    jobs = [Job.model_validate(row) for row in job_rows]
    return JobCollection(asset_id=asset_id, items=jobs, count=len(jobs), limit=max(1, limit))


def register_query_tools(mcp: FastMCP) -> None:
    """Register query-focused StreamKit MCP tools."""

    mcp.tool()(list_recent_assets)
    mcp.tool()(get_asset_details)
    mcp.tool()(list_asset_jobs)