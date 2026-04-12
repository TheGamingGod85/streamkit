from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from api.core.config import Settings
from api.models.asset import Asset, TransformParams
from api.routers.media import _build_playback_payload

from streamkit_mcp.context import StreamKitMCPContext


class StreamKitMCPError(RuntimeError):
    """Raised when the MCP server cannot fulfill a read-only request."""


def get_app_context(ctx: Context[ServerSession, Any]) -> StreamKitMCPContext:
    """Extract the typed lifespan context from the MCP request."""

    app_context = getattr(getattr(ctx, "request_context", None), "lifespan_context", None)
    if not isinstance(app_context, StreamKitMCPContext):
        raise StreamKitMCPError("StreamKit MCP context is not available.")
    return app_context


def is_public_asset_row(asset_row: Mapping[str, Any]) -> bool:
    """Return True when an asset is publicly readable."""

    return asset_row.get("user_id") in (None, "")


def require_public_asset(asset_row: Mapping[str, Any], asset_id: UUID | str) -> Asset:
    """Validate that an asset is public before exposing it through MCP."""

    if not is_public_asset_row(asset_row):
        raise StreamKitMCPError(f"Asset '{asset_id}' is private and is not exposed through the MCP server.")
    return Asset.model_validate(asset_row)


def build_transform_url(asset_id: UUID | str, params: TransformParams | None = None) -> str:
    """Build a relative image transform URL for a public asset."""

    query = ""
    if params is not None:
        payload = params.model_dump(by_alias=True, exclude_none=True)
        query = urlencode(payload)
    return f"/img/{asset_id}" if not query else f"/img/{asset_id}?{query}"


def build_media_links(asset: Asset, params: TransformParams | None = None) -> dict[str, object | None]:
    """Build canonical media URLs for a public asset."""

    playback = _build_playback_payload(asset)
    metadata = dict(asset.metadata or {})
    image_metadata = dict(metadata.get("image") or {})
    links: dict[str, object | None] = {
        "asset_url": playback.get("source_url"),
        "thumbnail_url": playback.get("thumbnail_url") or asset.thumbnail_url,
        "manifest_url": playback.get("manifest_url") or asset.master_url,
        "image_transform_url": None,
        "preview_url": image_metadata.get("preview_webp_url"),
        "smart_thumbnail_url": image_metadata.get("smart_thumbnail_url") or asset.thumbnail_url,
    }
    if asset.type == "image":
        links["image_transform_url"] = build_transform_url(asset.id, params)
    return links


def build_playback_payload(asset: Asset) -> dict[str, object | None]:
    """Return the same playback payload used by the API media routes."""

    return _build_playback_payload(asset)


def get_settings(ctx: Context[ServerSession, Any]) -> Settings:
    """Return the active application settings from the MCP context."""

    return get_app_context(ctx).settings


def get_supabase_service(ctx: Context[ServerSession, Any]):
    """Return the Supabase repository from the MCP context."""

    return get_app_context(ctx).supabase_service


def get_r2_service(ctx: Context[ServerSession, Any]):
    """Return the R2 service from the MCP context."""

    return get_app_context(ctx).r2_service