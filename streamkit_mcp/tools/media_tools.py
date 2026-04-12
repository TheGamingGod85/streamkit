from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from api.models.asset import TransformParams

from streamkit_mcp.context import StreamKitMCPContext
from streamkit_mcp.schemas import MediaLinks
from streamkit_mcp.tools._shared import build_media_links, get_app_context, require_public_asset


async def get_media_links(
    ctx: Context[ServerSession, StreamKitMCPContext],
    asset_id: str,
    width: int | None = None,
    height: int | None = None,
    format: str | None = None,
    quality: int = 80,
    crop: str | None = None,
) -> MediaLinks:
    """Return canonical player, manifest, thumbnail, and transform URLs for a public asset."""

    app_context = get_app_context(ctx)
    asset_row = await app_context.supabase_service.get_asset(asset_id)
    if asset_row is None:
        raise ValueError(f"Asset '{asset_id}' was not found.")
    asset = require_public_asset(asset_row, asset_id)
    params = TransformParams(width=width, height=height, format=format, quality=quality, crop=crop)
    links = build_media_links(asset, params if asset.type == "image" else None)
    return MediaLinks(
        asset_id=asset.id,
        asset_type=asset.type,
        player_url=f"/player/{asset.id}",
        asset_url=links.get("asset_url"),
        thumbnail_url=links.get("thumbnail_url") or asset.thumbnail_url,
        manifest_url=links.get("manifest_url") or asset.master_url,
        image_transform_url=links.get("image_transform_url"),
        preview_url=links.get("preview_url"),
        smart_thumbnail_url=links.get("smart_thumbnail_url"),
    )


def register_media_tools(mcp: FastMCP) -> None:
    """Register media-focused StreamKit MCP tools."""

    mcp.tool()(get_media_links)