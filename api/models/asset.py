from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AssetStatus = Literal["queued", "processing", "ready", "failed"]
AssetType = Literal["image", "video"]
JobStatus = Literal["queued", "processing", "ready", "failed"]
TransformFormat = Literal["jpeg", "jpg", "png", "webp", "avif"]
CropMode = Literal["smart", "center", "top", "bottom", "left", "right"]
FitMode = Literal["cover", "contain", "fill", "crop"]
FlipMode = Literal["h", "v"]


class Asset(BaseModel):
    """An uploaded media asset stored in R2 and tracked in Supabase."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "user_id": None,
                    "workspace_id": None,
                    "type": "image",
                    "status": "queued",
                    "original_filename": "photo.jpg",
                    "r2_base_path": "assets/11111111-1111-1111-1111-111111111111",
                    "master_url": None,
                    "thumbnail_url": None,
                    "metadata": {"content_type": "image/jpeg", "size_bytes": 1024},
                    "created_at": "2026-04-10T00:00:00Z",
                    "updated_at": "2026-04-10T00:00:00Z",
                }
            ]
        }
    )

    id: UUID
    user_id: UUID | None = None
    workspace_id: UUID | None = None
    type: AssetType
    status: AssetStatus = "queued"
    original_filename: str | None = None
    r2_base_path: str
    master_url: str | None = None
    thumbnail_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Job(BaseModel):
    """A processing job tracked alongside an asset."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "asset_id": "11111111-1111-1111-1111-111111111111",
                    "workspace_id": None,
                    "type": "upload",
                    "status": "queued",
                    "progress": 0,
                    "error": None,
                    "created_at": "2026-04-10T00:00:00Z",
                    "updated_at": "2026-04-10T00:00:00Z",
                }
            ]
        }
    )

    id: UUID
    asset_id: UUID
    workspace_id: UUID | None = None
    type: str
    status: JobStatus = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TransformParams(BaseModel):
    """Image transformation query parameters."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {"w": 800, "h": 600, "f": "webp", "q": 80, "crop": "smart"}
            ]
        },
    )

    width: int | None = Field(default=None, alias="w", ge=1, le=8192)
    height: int | None = Field(default=None, alias="h", ge=1, le=8192)
    format: TransformFormat | None = Field(default=None, alias="f")
    quality: int = Field(default=80, alias="q", ge=1, le=100)
    fit: FitMode | None = Field(default=None, alias="fit")
    crop: CropMode | None = Field(default=None, alias="crop")
    blur: float | None = Field(default=None, alias="blur", ge=0.0)
    sharp: float | None = Field(default=None, alias="sharp", ge=0.0)
    rotation: int | None = Field(default=None, alias="r")
    flip: FlipMode | None = Field(default=None, alias="flip")
    background: str | None = Field(default=None, alias="bg", description="Hex color")
    crop_x: int | None = Field(default=None, alias="cx", ge=0)
    crop_y: int | None = Field(default=None, alias="cy", ge=0)
    crop_w: int | None = Field(default=None, alias="cw", ge=1)
    crop_h: int | None = Field(default=None, alias="ch", ge=1)
    brightness: float | None = Field(default=None, alias="brightness", ge=0.0)
    contrast: float | None = Field(default=None, alias="contrast", ge=0.0)
    saturation: float | None = Field(default=None, alias="saturation", ge=0.0)


class UploadResponse(BaseModel):
    """Response returned after an upload has been accepted and stored."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "asset_id": "11111111-1111-1111-1111-111111111111",
                    "job_id": "22222222-2222-2222-2222-222222222222",
                    "status_url": "/status/11111111-1111-1111-1111-111111111111",
                    "message": "Upload stored successfully",
                }
            ]
        }
    )

    asset_id: UUID
    job_id: UUID | None = None
    status_url: str
    message: str = "Upload stored successfully"
    asset: Asset | None = None
    job: Job | None = None
