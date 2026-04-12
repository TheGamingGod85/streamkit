from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.models.asset import Asset, AssetType, Job


class ServerSummary(BaseModel):
    """High-level information about the StreamKit MCP server."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"server_name": "StreamKit", "app_env": "production"}]})

    server_name: str
    app_env: str
    transport: str
    streamable_http_path: str
    public_only: bool
    max_upload_size_mb: int
    r2_public_url: str | None = None
    supported_asset_types: list[AssetType] = Field(default_factory=lambda: ["image", "video"])
    routes: dict[str, str] = Field(default_factory=dict)
    services: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class AssetCollection(BaseModel):
    """A filtered collection of public StreamKit assets."""

    items: list[Asset] = Field(default_factory=list)
    count: int
    limit: int


class JobCollection(BaseModel):
    """A collection of jobs associated with a public asset."""

    asset_id: UUID
    items: list[Job] = Field(default_factory=list)
    count: int
    limit: int


class AssetDetails(BaseModel):
    """An asset record with playback information and recent jobs."""

    asset: Asset
    playback: dict[str, object | None]
    player_url: str
    jobs: list[Job] = Field(default_factory=list)


class MediaLinks(BaseModel):
    """Canonical media URLs for a public asset."""

    asset_id: UUID
    asset_type: AssetType
    player_url: str
    asset_url: str | None = None
    thumbnail_url: str | None = None
    manifest_url: str | None = None
    image_transform_url: str | None = None
    preview_url: str | None = None
    smart_thumbnail_url: str | None = None