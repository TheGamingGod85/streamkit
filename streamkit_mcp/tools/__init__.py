"""StreamKit MCP tools."""

from mcp.server.fastmcp import FastMCP

from streamkit_mcp.tools.media_tools import register_media_tools
from streamkit_mcp.tools.query_tools import register_query_tools
from streamkit_mcp.tools.setup_tools import register_setup_tools


def register_tools(mcp: FastMCP) -> None:
    """Register all StreamKit MCP tools on the provided server."""

    register_setup_tools(mcp)
    register_query_tools(mcp)
    register_media_tools(mcp)