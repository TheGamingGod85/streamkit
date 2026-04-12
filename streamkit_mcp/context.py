from __future__ import annotations

from dataclasses import dataclass

from api.core.config import Settings
from api.services.r2 import R2Service
from api.services.supabase_client import SupabaseRepository


@dataclass(slots=True)
class StreamKitMCPContext:
    """Shared services initialized for the MCP server lifespan."""

    settings: Settings
    r2_service: R2Service
    supabase_service: SupabaseRepository