from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from streamkit_mcp.context import StreamKitMCPContext
from streamkit_mcp.schemas import ServerSummary
from streamkit_mcp.tools._shared import get_app_context


async def get_server_summary(ctx: Context[ServerSession, StreamKitMCPContext]) -> ServerSummary:
    """Summarize the StreamKit deployment and what this MCP server exposes."""

    app_context = get_app_context(ctx)
    settings = app_context.settings
    return ServerSummary(
        server_name="StreamKit",
        app_env=settings.app_env,
        transport="streamable-http",
        streamable_http_path="/mcp",
        public_only=True,
        max_upload_size_mb=settings.max_upload_size_mb,
        r2_public_url=settings.r2_public_url or None,
        supported_asset_types=["image", "video"],
        routes={
            "health": "/health",
            "upload": "/upload",
            "status": "/status/{asset_id}",
            "assets": "/assets/{asset_id}",
            "player": "/player/{asset_id}",
            "image_transform": "/img/{asset_id}",
        },
        services={
            "r2": bool(settings.r2_account_id and settings.r2_bucket_name),
            "supabase": bool(settings.supabase_url and settings.supabase_service_role_key),
            "redis": bool(settings.redis_url),
        },
        notes=[
            "This MCP server exposes public assets only.",
            "Use the FastAPI API with Supabase JWT auth for owner-scoped access.",
        ],
    )


def register_setup_tools(mcp: FastMCP) -> None:
    """Register setup-focused StreamKit MCP tools."""

    mcp.tool()(get_server_summary)