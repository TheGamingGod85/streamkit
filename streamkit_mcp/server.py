from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import os
import re
import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from pydantic import BaseModel, Field

from api.core.config import Settings, get_settings
from api.models.asset import Asset, Job, TransformParams
from api.services.queue import QueuePublisher
from api.services.r2 import R2Service
from api.services.supabase_client import SupabaseRepository
from worker.tasks.image_task import enqueue_image_processing
from worker.tasks.video_task import enqueue_video_processing


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env", override=False)


ALLOWED_API_KEY_SCOPES = {"read", "write", "transform", "admin"}
DEFAULT_IMAGEKIT_API_BASE = "https://api.imagekit.io/v1"


class MCPError(RuntimeError):
    """Raised when the MCP server cannot complete a request."""


@dataclass(slots=True)
class RuntimeContext:
    settings: Settings
    http_client: httpx.AsyncClient
    supabase: SupabaseRepository
    r2: R2Service
    queue: QueuePublisher


def _ctx(ctx: Context[ServerSession, RuntimeContext]) -> RuntimeContext:
    runtime = getattr(getattr(ctx, "request_context", None), "lifespan_context", None)
    if not isinstance(runtime, RuntimeContext):
        raise MCPError("StreamKit MCP runtime context is not available.")
    return runtime


def _base_url(settings: Settings) -> str:
    return os.getenv("STREAMKIT_BASE_URL", "http://localhost:8000").rstrip("/")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or f"workspace-{uuid4().hex[:8]}"


def _normalize_scopes(scopes: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    for scope in scopes or []:
        clean = str(scope).strip().lower()
        if clean and clean in ALLOWED_API_KEY_SCOPES and clean not in normalized:
            normalized.append(clean)
    return normalized or ["read", "write"]


def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)


def _query_params_from_kwargs(**kwargs: Any) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            params[key] = "true" if value else "false"
        else:
            params[key] = str(value)
    return params


def _transform_params_to_url_params(params: TransformParams) -> dict[str, str]:
    payload = params.model_dump(by_alias=True, exclude_none=True)
    return {key: str(value) for key, value in payload.items()}


def _build_transform_params(
    *,
    width: int | None = None,
    height: int | None = None,
    format: str | None = None,
    quality: int = 80,
    fit: str | None = None,
    crop: str | None = None,
    blur: float | None = None,
    rotation: int | None = None,
    flip: str | None = None,
    brightness: float | None = None,
    contrast: float | None = None,
    saturation: float | None = None,
) -> TransformParams:
    return TransformParams(
        width=width,
        height=height,
        format=format,
        quality=quality,
        fit=fit,
        crop=crop,
        blur=blur,
        rotation=rotation,
        flip=flip,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
    )


def _media_url(base_url: str, asset_id: str, params: TransformParams | None = None) -> str:
    query = urlencode(_transform_params_to_url_params(params)) if params is not None else ""
    return f"{base_url}/media/{asset_id}" + (f"?{query}" if query else "")


def _player_url(base_url: str, asset_id: str) -> str:
    return f"{base_url}/player/{asset_id}"


def _ik_url(base_url: str, asset_path: str, tr: str | None = None) -> str:
    encoded_path = "/".join(part for part in asset_path.split("/") if part)
    if tr:
        return f"{base_url}/ik/{encoded_path}?tr={tr}"
    return f"{base_url}/ik/{encoded_path}"


def _cloudinary_url(base_url: str, cloud_name: str, asset_path: str, transformations: str | None = None) -> str:
    encoded_path = "/".join(part for part in asset_path.split("/") if part)
    if transformations:
        return f"{base_url}/cloudinary/{cloud_name}/image/upload/{transformations}/{encoded_path}"
    return f"{base_url}/cloudinary/{cloud_name}/image/upload/{encoded_path}"


def _extract_path_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.lstrip("/")


def _public_url_candidates(asset_row: dict[str, Any], settings: Settings) -> list[str]:
    metadata = dict(asset_row.get("metadata") or {})
    image_metadata = dict(metadata.get("image") or {})
    video_metadata = dict(metadata.get("video") or {})
    candidates: list[str] = []
    for key in (
        metadata.get("object_key"),
        asset_row.get("master_url"),
        asset_row.get("thumbnail_url"),
        image_metadata.get("preview_webp_url"),
        image_metadata.get("preview_avif_url"),
        image_metadata.get("smart_thumbnail_url"),
    ):
        if isinstance(key, str) and key:
            if key.startswith("http://") or key.startswith("https://"):
                candidates.append(key)
            else:
                candidates.append(f"{settings.r2_public_url.rstrip('/')}/{key.lstrip('/')}")
    for variant in video_metadata.get("qualities") or []:
        if isinstance(variant, dict):
            playlist_key = variant.get("playlist_key")
            if isinstance(playlist_key, str) and playlist_key:
                candidates.append(f"{settings.r2_public_url.rstrip('/')}/{playlist_key.lstrip('/')}")
            for segment_key in variant.get("segment_keys") or []:
                if isinstance(segment_key, str) and segment_key:
                    candidates.append(f"{settings.r2_public_url.rstrip('/')}/{segment_key.lstrip('/')}")
    master_key = video_metadata.get("master_playlist_key")
    if isinstance(master_key, str) and master_key:
        candidates.append(f"{settings.r2_public_url.rstrip('/')}/{master_key.lstrip('/')}")
    return list(dict.fromkeys(candidates))


def _extract_r2_object_keys(asset_row: dict[str, Any], settings: Settings) -> list[str]:
    keys: list[str] = []
    metadata = dict(asset_row.get("metadata") or {})
    image_metadata = dict(metadata.get("image") or {})
    video_metadata = dict(metadata.get("video") or {})

    def add_candidate(value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            path = parsed.path.lstrip("/")
            if path:
                keys.append(path)
        else:
            keys.append(value.lstrip("/"))

    add_candidate(metadata.get("object_key"))
    add_candidate(asset_row.get("master_url"))
    add_candidate(asset_row.get("thumbnail_url"))
    add_candidate(image_metadata.get("preview_webp_url"))
    add_candidate(image_metadata.get("preview_avif_url"))
    add_candidate(image_metadata.get("smart_thumbnail_url"))
    add_candidate(image_metadata.get("preview_webp_key"))
    add_candidate(image_metadata.get("smart_thumbnail_key"))
    add_candidate(image_metadata.get("preview_avif_key"))

    for quality_row in video_metadata.get("qualities") or []:
        if not isinstance(quality_row, dict):
            continue
        add_candidate(quality_row.get("playlist_key"))
        for segment_key in quality_row.get("segment_keys") or []:
            add_candidate(segment_key)

    add_candidate(video_metadata.get("master_playlist_key"))
    return list(dict.fromkeys(key for key in keys if key))


async def _streamkit_request(
    runtime: RuntimeContext,
    method: str,
    path: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request_headers = dict(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    response = await runtime.http_client.request(
        method,
        path,
        params=params,
        json=json_body,
        data=data,
        files=files,
        headers=request_headers,
    )
    response.raise_for_status()
    return response


async def _get_asset_row(runtime: RuntimeContext, asset_id: str) -> dict[str, Any]:
    asset_row = await runtime.supabase.get_asset(asset_id)
    if asset_row is None:
        raise MCPError(f"Asset '{asset_id}' was not found.")
    return asset_row


async def _get_job_row(runtime: RuntimeContext, job_id: str) -> dict[str, Any]:
    job_row = await runtime.supabase.get_job(job_id)
    if job_row is None:
        raise MCPError(f"Job '{job_id}' was not found.")
    return job_row


def _estimate_image_size(current_bytes: int, format_name: str, transparency: bool) -> int:
    format_name = format_name.lower()
    if format_name == "avif":
        factor = 0.24 if not transparency else 0.32
    elif format_name == "webp":
        factor = 0.34 if not transparency else 0.44
    else:
        factor = 0.6
    return max(1, int(current_bytes * factor))


def _estimate_savings_percentage(current_bytes: int, target_bytes: int) -> float:
    if current_bytes <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - (target_bytes / current_bytes)) * 100.0))


def _hamming_distance(a: str, b: str) -> int:
    max_len = max(len(a), len(b))
    distance = 0
    for index in range(max_len):
        left = a[index] if index < len(a) else None
        right = b[index] if index < len(b) else None
        if left != right:
            distance += 1
    return distance


async def _image_has_transparency(runtime: RuntimeContext, asset_row: dict[str, Any]) -> bool:
    metadata = dict(asset_row.get("metadata") or {})
    image_metadata = dict(metadata.get("image") or {})
    candidates = _public_url_candidates(asset_row, runtime.settings)
    image_url = (
        image_metadata.get("smart_thumbnail_url")
        or image_metadata.get("preview_webp_url")
        or asset_row.get("thumbnail_url")
        or (candidates[0] if candidates else None)
    )
    if not isinstance(image_url, str) or not image_url:
        return False
    try:
        response = await runtime.http_client.get(image_url)
        response.raise_for_status()
        from PIL import Image
        import io

        with Image.open(io.BytesIO(response.content)) as image:
            if image.mode in {"RGBA", "LA"}:
                return True
            if image.mode == "P" and "transparency" in image.info:
                return True
            return bool(getattr(image, "has_transparency_data", False))
    except Exception:
        return False


async def _head_or_get_bytes(runtime: RuntimeContext, url: str) -> int | None:
    try:
        response = await runtime.http_client.head(url, follow_redirects=True)
        if response.status_code < 400:
            content_length = response.headers.get("content-length")
            if content_length:
                return int(content_length)
    except Exception:
        pass
    try:
        response = await runtime.http_client.get(url, follow_redirects=True)
        response.raise_for_status()
        return len(response.content)
    except Exception:
        return None


async def _analyze_image_asset(runtime: RuntimeContext, asset_row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(asset_row.get("metadata") or {})
    image_metadata = dict(metadata.get("image") or {})
    current_size = int(metadata.get("size_bytes") or 0)
    if current_size <= 0:
        candidates = _public_url_candidates(asset_row, runtime.settings)
        if candidates:
            guessed = await _head_or_get_bytes(runtime, candidates[0])
            current_size = int(guessed or 0)
    if current_size <= 0:
        current_size = 1

    transparency = await _image_has_transparency(runtime, asset_row)
    width = int(image_metadata.get("source_width") or 0)
    height = int(image_metadata.get("source_height") or 0)

    webp_url = image_metadata.get("preview_webp_url") or asset_row.get("thumbnail_url")
    avif_url = image_metadata.get("preview_avif_url")
    webp_bytes = await _head_or_get_bytes(runtime, webp_url) if isinstance(webp_url, str) else None
    avif_bytes = await _head_or_get_bytes(runtime, avif_url) if isinstance(avif_url, str) else None
    estimated_webp = int(webp_bytes or _estimate_image_size(current_size, "webp", transparency))
    estimated_avif = int(avif_bytes or _estimate_image_size(current_size, "avif", transparency))
    recommended_format = "avif" if estimated_avif <= estimated_webp else "webp"
    best_estimate = min(estimated_avif, estimated_webp)

    return {
        "asset_id": str(asset_row.get("id")),
        "workspace_id": str(asset_row.get("workspace_id") or ""),
        "current_size_bytes": current_size,
        "estimated_avif_size_bytes": estimated_avif,
        "estimated_webp_size_bytes": estimated_webp,
        "potential_savings_percentage": round(_estimate_savings_percentage(current_size, best_estimate), 2),
        "recommended_format": recommended_format,
        "is_oversized": bool(width >= 2000 or height >= 2000 or current_size >= 2_000_000),
        "has_transparency": transparency,
        "dimensions": {"width": width or None, "height": height or None},
        "source_format": (image_metadata.get("source_format") or metadata.get("format") or asset_row.get("type") or "image"),
    }


async def _analyze_asset_for_workspace_scan(runtime: RuntimeContext, asset_row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "asset_id": str(asset_row.get("id")),
        "name": asset_row.get("original_filename") or asset_row.get("id"),
        "type": asset_row.get("type"),
        "recommended_action": "noop",
        "estimated_savings_bytes": 0,
        "current_size_bytes": int((asset_row.get("metadata") or {}).get("size_bytes") or 0),
    }
    if asset_row.get("type") == "image":
        analysis = await _analyze_image_asset(runtime, asset_row)
        current = int(analysis["current_size_bytes"])
        best = min(int(analysis["estimated_avif_size_bytes"]), int(analysis["estimated_webp_size_bytes"]))
        result.update(
            {
                "recommended_action": f"Convert to {analysis['recommended_format'].upper()} and resize if needed",
                "estimated_savings_bytes": max(0, current - best),
                "analysis": analysis,
            }
        )
    return result


def _group_duplicate_candidates(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for asset in assets:
        blurhash_value = asset.get("blurhash")
        if not isinstance(blurhash_value, str) or not blurhash_value:
            continue
        placed = False
        for group in groups:
            representative = group["members"][0]
            distance = _hamming_distance(blurhash_value, representative["blurhash"])
            if distance < 10:
                group["members"].append(asset)
                group["distances"].append(distance)
                placed = True
                break
        if not placed:
            groups.append({"members": [asset], "distances": []})
    return groups


def _build_transform_report_url(base_url: str, asset_id: str, params: TransformParams) -> str:
    return _media_url(base_url, asset_id, params)


def _parse_legacy_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    host = parsed.netloc.lower()
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return {"host": host, "path": path, "query": parsed.query, **query_items}


def _convert_imagekit_transform(tr: str | None) -> str | None:
    if not tr:
        return None
    parts: list[str] = []
    for chunk in tr.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            continue
        key, value = chunk.split("-", 1)
        key = key.strip()
        value = value.strip()
        if key == "c" and value == "maintain_ratio":
            parts.append("fit=contain")
        elif key == "f":
            parts.append(f"f={value}")
        elif key == "w":
            parts.append(f"w={value}")
        elif key == "h":
            parts.append(f"h={value}")
        elif key == "q":
            parts.append(f"q={value}")
        elif key == "r":
            parts.append(f"r={value}")
        elif key == "bl":
            parts.append(f"blur={value}")
        elif key == "b":
            parts.append(f"bg={value}")
    return ",".join(parts) if parts else None


def _convert_cloudinary_transformations(transformations: str | None) -> str | None:
    if not transformations:
        return None
    parts = [chunk.strip() for chunk in transformations.split("/") if chunk.strip()]
    return "/".join(parts) if parts else None


async def _list_imagekit_files(private_key: str, folder: str | None = None, page_size: int = 100) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=DEFAULT_IMAGEKIT_API_BASE, auth=(private_key, ""), timeout=60.0) as client:
        skip = 0
        while True:
            params = {"skip": skip, "limit": page_size}
            if folder:
                params["path"] = folder
            response = await client.get("/files", params=params)
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise MCPError("ImageKit API returned an unexpected response shape.")
            files.extend(batch)
            if len(batch) < page_size:
                break
            skip += page_size
    return files


@dataclass(slots=True)
class AssetDeletePlan:
    asset_id: str
    storage_keys: list[str]
    deleted_tables: dict[str, int]


async def _delete_asset_everywhere(runtime: RuntimeContext, asset_id: str) -> AssetDeletePlan:
    asset_row = await _get_asset_row(runtime, asset_id)
    storage_keys = _extract_r2_object_keys(asset_row, runtime.settings)
    for key in storage_keys:
        try:
            await runtime.r2.delete_file(key)
        except Exception:
            continue

    deleted_tables: dict[str, int] = {}
    supabase = runtime.supabase.client
    for table, column in (("media_events", "asset_id"), ("jobs", "asset_id")):
        response = await supabase.table(table).delete().eq(column, str(asset_id)).execute()
        deleted_tables[table] = len(response.data or [])
    response = await supabase.table("assets").delete().eq("id", str(asset_id)).execute()
    deleted_tables["assets"] = len(response.data or [])
    return AssetDeletePlan(asset_id=asset_id, storage_keys=storage_keys, deleted_tables=deleted_tables)


def create_server() -> FastMCP:
    """Create the StreamKit MCP server."""

    return FastMCP(
        "StreamKit",
        lifespan=lifespan,
        instructions=(
            "Production-grade StreamKit MCP server for asset management, transforms, workspaces, presets, analytics, origins, webhooks, and media intelligence. "
            "This server can read and manage workspace-scoped resources using the StreamKit API and the Supabase service role."
        ),
    )


@asynccontextmanager
async def lifespan(_server: FastMCP):
    """Initialize shared services for MCP tool calls."""

    settings = get_settings()
    http_client = httpx.AsyncClient(base_url=_base_url(settings), timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True)
    supabase = await SupabaseRepository.create(settings)
    r2 = R2Service(settings)
    queue = await QueuePublisher.create(settings)
    runtime = RuntimeContext(settings=settings, http_client=http_client, supabase=supabase, r2=r2, queue=queue)
    try:
        yield runtime
    finally:
        await queue.aclose()
        await supabase.aclose()
        await http_client.aclose()


mcp = create_server()


@mcp.tool()
async def asset_upload(
    ctx: Context[ServerSession, RuntimeContext],
    file_path: str,
    workspace_id: str,
    private_asset: bool = False,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Upload a local file to StreamKit by POSTing it to the upload endpoint."""

    runtime = _ctx(ctx)
    path = Path(file_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise MCPError(f"File not found: {file_path}")

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = path.read_bytes()
    response = await _streamkit_request(
        runtime,
        "POST",
        "/upload",
        token=access_token,
        data={"workspace_id": workspace_id, "private_asset": str(private_asset).lower()},
        files={"file": (path.name, data, mime_type)},
    )
    payload = response.json().get("data", {})
    return {
        "uploaded": True,
        "workspace_id": workspace_id,
        "file_name": path.name,
        "response": payload,
    }


@mcp.tool()
async def asset_get_status(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Get the current processing status and job history for any asset."""

    runtime = _ctx(ctx)
    try:
        response = await _streamkit_request(runtime, "GET", f"/status/{asset_id}", token=access_token)
        payload = response.json().get("data", {})
        asset_row = await _get_asset_row(runtime, asset_id)
    except Exception:
        asset_row = await _get_asset_row(runtime, asset_id)
        jobs = await runtime.supabase.list_jobs_for_asset(asset_id, limit=20)
        payload = {"asset": asset_row, "status": asset_row.get("status"), "jobs": jobs}
    if "jobs" not in payload:
        payload["jobs"] = await runtime.supabase.list_jobs_for_asset(asset_id, limit=20)
    return payload


@mcp.tool()
async def asset_list(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    status: str | None = None,
    type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List assets in a workspace with optional status and type filters."""

    runtime = _ctx(ctx)
    query = runtime.supabase.client.table("assets").select("*").eq("workspace_id", workspace_id).order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    if type:
        query = query.eq("type", type)
    response = await query.limit(max(1, limit + offset)).execute()
    rows = [dict(row) for row in response.data or []]
    return {"workspace_id": workspace_id, "count": len(rows[offset : offset + limit]), "items": rows[offset : offset + limit]}


@mcp.tool()
async def asset_delete(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
) -> dict[str, Any]:
    """Delete an asset, its database rows, and its derived storage objects."""

    runtime = _ctx(ctx)
    plan = await _delete_asset_everywhere(runtime, asset_id)
    return {
        "deleted": True,
        "asset_id": plan.asset_id,
        "storage_keys": plan.storage_keys,
        "deleted_tables": plan.deleted_tables,
    }


@mcp.tool()
async def job_get(
    ctx: Context[ServerSession, RuntimeContext],
    job_id: str,
) -> dict[str, Any]:
    """Get the status and progress for a specific job."""

    runtime = _ctx(ctx)
    job_row = await _get_job_row(runtime, job_id)
    asset_row = await runtime.supabase.get_asset(job_row.get("asset_id"))
    return {"job": job_row, "asset": asset_row}


@mcp.tool()
async def job_list_failed(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List all failed jobs in a workspace."""

    runtime = _ctx(ctx)
    response = (
        await runtime.supabase.client.table("jobs")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("status", "failed")
        .order("created_at", desc=True)
        .limit(max(1, limit + offset))
        .execute()
    )
    rows = [dict(row) for row in response.data or []]
    return {"workspace_id": workspace_id, "count": len(rows[offset : offset + limit]), "items": rows[offset : offset + limit]}


@mcp.tool()
async def job_retry(
    ctx: Context[ServerSession, RuntimeContext],
    job_id: str,
) -> dict[str, Any]:
    """Retry a failed job by re-queuing it."""

    runtime = _ctx(ctx)
    job_row = await _get_job_row(runtime, job_id)
    if str(job_row.get("status")) != "failed":
        raise MCPError(f"Job '{job_id}' is not failed and cannot be retried.")

    asset_id = str(job_row.get("asset_id"))
    asset_row = await _get_asset_row(runtime, asset_id)
    object_key = _extract_r2_object_keys(asset_row, runtime.settings)
    source_object_key = object_key[0] if object_key else None
    if source_object_key is None:
        raise MCPError(f"Could not determine the storage object key for asset '{asset_id}'.")

    await runtime.supabase.client.table("jobs").update(
        {"status": "queued", "progress": 0, "error": None, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", job_id).execute()

    job_type = str(job_row.get("type") or "")
    if job_type == "upload":
        payload = {
            "asset_type": asset_row.get("type"),
            "content_type": (asset_row.get("metadata") or {}).get("content_type"),
            "object_key": source_object_key,
            "r2_base_path": asset_row.get("r2_base_path"),
            "original_filename": asset_row.get("original_filename"),
            "size_bytes": (asset_row.get("metadata") or {}).get("size_bytes"),
            "workspace_id": asset_row.get("workspace_id"),
        }
        entry_id = await runtime.queue.publish_job(asset_id=asset_id, job_id=job_id, job_type="upload", payload=payload)
        return {"requeued": True, "job_id": job_id, "queue_entry_id": entry_id, "mode": "upload"}

    if asset_row.get("type") == "image":
        task_id = enqueue_image_processing(asset_id, job_id, source_object_key)
        return {"requeued": True, "job_id": job_id, "celery_task_id": task_id, "mode": "image"}

    if asset_row.get("type") == "video":
        task_id = enqueue_video_processing(asset_id, job_id, source_object_key)
        return {"requeued": True, "job_id": job_id, "celery_task_id": task_id, "mode": "video"}

    entry_id = await runtime.queue.publish_job(asset_id=asset_id, job_id=job_id, job_type=job_type or "retry", payload={"object_key": source_object_key})
    return {"requeued": True, "job_id": job_id, "queue_entry_id": entry_id, "mode": "generic"}


@mcp.tool()
async def transform_apply_image(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
    width: int | None = None,
    height: int | None = None,
    format: str | None = None,
    quality: int = 80,
    fit: str | None = None,
    crop: str | None = None,
    blur: float | None = None,
    rotation: int | None = None,
    flip: str | None = None,
    brightness: float | None = None,
    contrast: float | None = None,
    saturation: float | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Apply a real-time image transformation by calling the StreamKit media endpoint."""

    runtime = _ctx(ctx)
    params = _build_transform_params(
        width=width,
        height=height,
        format=format,
        quality=quality,
        fit=fit,
        crop=crop,
        blur=blur,
        rotation=rotation,
        flip=flip,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
    )
    url = _media_url(_base_url(runtime.settings), asset_id, params)
    response = await _streamkit_request(runtime, "HEAD", f"/media/{asset_id}", token=access_token, params=params.model_dump(by_alias=True, exclude_none=True))
    return {
        "asset_id": asset_id,
        "url": url,
        "content_type": response.headers.get("content-type"),
        "content_length": response.headers.get("content-length"),
        "cache_status": response.headers.get("x-cache"),
        "width": response.headers.get("x-image-width"),
        "height": response.headers.get("x-image-height"),
        "format": response.headers.get("x-image-format"),
    }


@mcp.tool()
async def transform_generate_url(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
    width: int | None = None,
    height: int | None = None,
    format: str | None = None,
    quality: int = 80,
    fit: str | None = None,
    crop: str | None = None,
    blur: float | None = None,
    rotation: int | None = None,
    flip: str | None = None,
    brightness: float | None = None,
    contrast: float | None = None,
    saturation: float | None = None,
) -> dict[str, Any]:
    """Generate a transformation URL without making any HTTP request."""

    runtime = _ctx(ctx)
    params = _build_transform_params(
        width=width,
        height=height,
        format=format,
        quality=quality,
        fit=fit,
        crop=crop,
        blur=blur,
        rotation=rotation,
        flip=flip,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
    )
    return {"asset_id": asset_id, "url": _media_url(_base_url(runtime.settings), asset_id, params)}


@mcp.tool()
async def transform_generate_imagekit_url(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
    transformation_string: str | None = None,
) -> dict[str, Any]:
    """Generate an ImageKit-compatible URL for a StreamKit asset."""

    runtime = _ctx(ctx)
    asset_row = await _get_asset_row(runtime, asset_id)
    asset_path = _extract_r2_object_keys(asset_row, runtime.settings)
    path = asset_path[0] if asset_path else _as_text((asset_row.get("metadata") or {}).get("object_key"), asset_id)
    tr = _convert_imagekit_transform(transformation_string)
    return {"asset_id": asset_id, "url": _ik_url(_base_url(runtime.settings), path, tr), "transformation_string": tr}


@mcp.tool()
async def transform_generate_cloudinary_url(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
    cloud_name: str,
    transformation_string: str | None = None,
) -> dict[str, Any]:
    """Generate a Cloudinary-compatible URL for a StreamKit asset."""

    runtime = _ctx(ctx)
    asset_row = await _get_asset_row(runtime, asset_id)
    asset_path = _extract_r2_object_keys(asset_row, runtime.settings)
    path = asset_path[0] if asset_path else _as_text((asset_row.get("metadata") or {}).get("object_key"), asset_id)
    transformations = _convert_cloudinary_transformations(transformation_string)
    return {
        "asset_id": asset_id,
        "cloud_name": cloud_name,
        "url": _cloudinary_url(_base_url(runtime.settings), cloud_name, path, transformations),
        "transformations": transformations,
    }


@mcp.tool()
async def workspace_create(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    name: str,
    organization_name: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """Create a new workspace, organization, and initial API key."""

    runtime = _ctx(ctx)
    slug = _slugify(workspace_id)
    existing = await runtime.supabase.client.table("workspaces").select("id").eq("slug", slug).limit(1).execute()
    if existing.data:
        raise MCPError(f"Workspace slug '{slug}' already exists.")

    organization = (
        await runtime.supabase.client.table("organizations")
        .insert({"name": organization_name or name, "owner_user_id": owner_user_id})
        .execute()
    ).data[0]
    workspace = (
        await runtime.supabase.client.table("workspaces")
        .insert({"org_id": organization["id"], "name": name, "slug": slug, "r2_prefix": f"workspaces/{slug}"})
        .execute()
    ).data[0]
    raw_key = "sk_test_" + base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8").rstrip("=")
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    metadata = (
        await runtime.supabase.client.table("api_keys")
        .insert({"workspace_id": workspace["id"], "name": f"{name} API Key", "key_hash": key_hash, "scopes": ["read", "write", "transform"]})
        .execute()
    ).data[0]
    return {"workspace": workspace, "organization": organization, "api_key": raw_key, "metadata": metadata}


@mcp.tool()
async def workspace_get(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """Get details for an existing workspace."""

    runtime = _ctx(ctx)
    workspace_rows = await runtime.supabase.client.table("workspaces").select("*").eq("id", workspace_id).limit(1).execute()
    if not workspace_rows.data:
        raise MCPError(f"Workspace '{workspace_id}' was not found.")
    workspace = workspace_rows.data[0]
    org_rows = await runtime.supabase.client.table("organizations").select("*").eq("id", workspace["org_id"]).limit(1).execute()
    api_key_rows = await runtime.supabase.client.table("api_keys").select("id, name, scopes, created_at, last_used_at").eq("workspace_id", workspace_id).execute()
    assets_rows = await runtime.supabase.client.table("assets").select("id").eq("workspace_id", workspace_id).execute()
    return {
        "workspace": workspace,
        "organization": org_rows.data[0] if org_rows.data else None,
        "api_keys": api_key_rows.data or [],
        "counts": {"assets": len(assets_rows.data or []), "api_keys": len(api_key_rows.data or [])},
    }


@mcp.tool()
async def workspace_create_api_key(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    name: str,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Create an API key for a workspace with specific scopes."""

    runtime = _ctx(ctx)
    normalized_scopes = _normalize_scopes(scopes)
    raw_key = "sk_test_" + base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8").rstrip("=")
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    metadata = (
        await runtime.supabase.client.table("api_keys")
        .insert({"workspace_id": workspace_id, "name": name, "key_hash": key_hash, "scopes": normalized_scopes})
        .execute()
    ).data[0]
    return {"api_key": raw_key, "metadata": metadata}


@mcp.tool()
async def workspace_list_api_keys(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """List all API keys for a workspace without returning the secret value."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("api_keys")
        .select("id, name, scopes, created_at, last_used_at")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    return {"workspace_id": workspace_id, "count": len(rows), "items": rows}


@mcp.tool()
async def preset_create(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    name: str,
    transformations: dict[str, Any],
) -> dict[str, Any]:
    """Create a named transformation preset."""

    runtime = _ctx(ctx)
    response = (
        await runtime.supabase.client.table("presets")
        .insert({"workspace_id": workspace_id, "name": name, "transformations": transformations})
        .execute()
    ).data[0]
    return {"preset": response}


@mcp.tool()
async def preset_list(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """List all presets in a workspace."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("presets")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("name", desc=False)
        .execute()
    ).data or []
    return {"workspace_id": workspace_id, "count": len(rows), "items": rows}


@mcp.tool()
async def preset_apply(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    preset_name: str,
    asset_id: str,
) -> dict[str, Any]:
    """Apply a named preset to an asset and return the final StreamKit URL."""

    runtime = _ctx(ctx)
    preset_rows = (
        await runtime.supabase.client.table("presets")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("name", preset_name)
        .limit(1)
        .execute()
    ).data or []
    if not preset_rows:
        raise MCPError(f"Preset '{preset_name}' was not found in workspace '{workspace_id}'.")
    preset = preset_rows[0]
    asset_row = await _get_asset_row(runtime, asset_id)
    if str(asset_row.get("workspace_id")) != str(workspace_id):
        raise MCPError(f"Asset '{asset_id}' does not belong to workspace '{workspace_id}'.")
    transformations = preset.get("transformations") or preset.get("params") or {}
    url = _media_url(_base_url(runtime.settings), asset_id, TransformParams.model_validate(transformations))
    return {"preset": preset, "asset": asset_row, "url": url}


@mcp.tool()
async def analytics_summary(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """Get a workspace analytics summary including requests, cache hit rate, bandwidth saved, response time, and format breakdown."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("media_events")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    total_requests = len(rows)
    format_breakdown: dict[str, int] = defaultdict(int)
    response_times: list[int] = []
    estimated_bytes_saved = 0
    probable_cache_hits = 0
    for row in rows:
        format_breakdown[str(row.get("format_served") or "unknown")] += 1
        if row.get("response_time_ms") is not None:
            response_times.append(int(row["response_time_ms"]))
        bytes_saved = int(row.get("bytes_saved") or 0)
        estimated_bytes_saved += max(0, bytes_saved)
        if int(row.get("response_time_ms") or 9999) <= 80 or bytes_saved > 0:
            probable_cache_hits += 1
    average_response_time_ms = round(statistics.mean(response_times), 2) if response_times else 0.0
    cache_hit_percentage = round((probable_cache_hits / total_requests) * 100.0, 2) if total_requests else 0.0
    return {
        "workspace_id": workspace_id,
        "total_requests": total_requests,
        "cache_hit_percentage": cache_hit_percentage,
        "bandwidth_saved_bytes": estimated_bytes_saved,
        "bandwidth_saved_megabytes": round(estimated_bytes_saved / (1024 * 1024), 3),
        "average_response_time_ms": average_response_time_ms,
        "format_breakdown": dict(format_breakdown),
    }


@mcp.tool()
async def analytics_top_assets(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Get the most requested assets in a workspace."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("media_events")
        .select("*")
        .eq("workspace_id", workspace_id)
        .execute()
    ).data or []
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset_id = str(row.get("asset_id") or "")
        if not asset_id:
            continue
        bucket = counts.setdefault(asset_id, {"asset_id": asset_id, "count": 0, "bytes_saved": 0, "format_breakdown": defaultdict(int)})
        bucket["count"] += 1
        bucket["bytes_saved"] += int(row.get("bytes_saved") or 0)
        bucket["format_breakdown"][str(row.get("format_served") or "unknown")] += 1
    ranked = sorted(counts.values(), key=lambda item: (item["count"], item["bytes_saved"]), reverse=True)[: max(1, limit)]
    for item in ranked:
        item["format_breakdown"] = dict(item["format_breakdown"])
    return {"workspace_id": workspace_id, "count": len(ranked), "items": ranked}


@mcp.tool()
async def analytics_error_report(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """Get transformation and processing errors grouped by asset."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("jobs")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("status", "failed")
        .order("created_at", desc=True)
        .execute()
    ).data or []
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset_id = str(row.get("asset_id") or "unknown")
        bucket = grouped.setdefault(asset_id, {"asset_id": asset_id, "failures": [], "count": 0})
        bucket["count"] += 1
        bucket["failures"].append({
            "job_id": str(row.get("id")),
            "type": row.get("type"),
            "status": row.get("status"),
            "error": row.get("error"),
            "progress": row.get("progress"),
            "created_at": row.get("created_at"),
        })
    return {"workspace_id": workspace_id, "count": len(grouped), "items": list(grouped.values())}


@mcp.tool()
async def origin_register(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    name: str,
    provider: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Register an external storage bucket or HTTP origin."""

    runtime = _ctx(ctx)
    provider_normalized = provider.strip().lower()
    normalized_config = dict(config)
    if provider_normalized == "r2":
        normalized_config.setdefault("endpoint", f"https://{runtime.settings.r2_account_id}.r2.cloudflarestorage.com")
        normalized_config.setdefault("path_style_access", True)
        normalized_config.setdefault("bucket_name", normalized_config.get("bucket"))
    if provider_normalized in {"azure_blob", "gcs"}:
        source_url = normalized_config.get("source_url") or normalized_config.get("base_url")
        if not source_url:
            raise MCPError("Azure Blob and GCS origins need a source_url or base_url in config.")
        normalized_config.setdefault("source_url", source_url)
        normalized_config.setdefault("base_url", source_url)
    if provider_normalized == "http" and not (normalized_config.get("source_url") or normalized_config.get("base_url")):
        raise MCPError("HTTP origins need a source_url or base_url in config.")
    if "sample_paths" in normalized_config and not isinstance(normalized_config["sample_paths"], list):
        normalized_config["sample_paths"] = [str(normalized_config["sample_paths"])]
    normalized_config.setdefault("provider", provider_normalized)

    response = (
        await runtime.supabase.client.table("origins")
        .insert({"workspace_id": workspace_id, "name": name, "type": provider_normalized, "config": normalized_config})
        .execute()
    ).data[0]
    return {"origin": response}


@mcp.tool()
async def origin_proxy_url(
    ctx: Context[ServerSession, RuntimeContext],
    origin_id: str,
    asset_path: str,
    width: int | None = None,
    height: int | None = None,
    format: str | None = None,
    quality: int = 80,
    fit: str | None = None,
) -> dict[str, Any]:
    """Generate a proxy URL for serving and optimizing an asset from a registered origin."""

    runtime = _ctx(ctx)
    params = _build_transform_params(width=width, height=height, format=format, quality=quality, fit=fit)
    query = urlencode(_transform_params_to_url_params(params))
    return {
        "origin_id": origin_id,
        "asset_path": asset_path,
        "url": f"{_base_url(runtime.settings)}/proxy/{origin_id}/{asset_path.lstrip('/')}" + (f"?{query}" if query else ""),
    }


@mcp.tool()
async def origin_test(
    ctx: Context[ServerSession, RuntimeContext],
    origin_id: str,
    sample_path: str | None = None,
) -> dict[str, Any]:
    """Test an origin connection by checking reachability and returning latency."""

    runtime = _ctx(ctx)
    origin_rows = await runtime.supabase.client.table("origins").select("*").eq("id", origin_id).limit(1).execute()
    if not origin_rows.data:
        raise MCPError(f"Origin '{origin_id}' was not found.")
    origin = origin_rows.data[0]
    config = dict(origin.get("config") or {})
    paths = config.get("sample_paths") or []
    path = sample_path or (paths[0] if paths else None)
    if not path:
        raise MCPError("Provide a sample_path or store sample_paths in the origin config.")
    params = {"w": 32, "h": 32, "f": "webp", "q": 40}
    started_at = time.perf_counter()
    response = await runtime.http_client.get(f"/proxy/{origin_id}/{path.lstrip('/')}", params=params)
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return {
        "origin_id": origin_id,
        "reachable": response.status_code < 400,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "content_type": response.headers.get("content-type"),
        "cache_status": response.headers.get("x-cache"),
        "sample_path": path,
    }


@mcp.tool()
async def webhook_register(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
    url: str,
    events: list[str],
    secret: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Register a webhook URL for workspace events."""

    runtime = _ctx(ctx)
    response = (
        await runtime.supabase.client.table("webhooks")
        .insert({"workspace_id": workspace_id, "url": url, "events": events, "secret": secret, "is_active": is_active})
        .execute()
    ).data[0]
    return {"webhook": response}


@mcp.tool()
async def webhook_list(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """List all registered webhooks for a workspace."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("webhooks")
        .select("id, workspace_id, url, events, is_active, created_at")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    return {"workspace_id": workspace_id, "count": len(rows), "items": rows}


@mcp.tool()
async def media_analyze_asset(
    ctx: Context[ServerSession, RuntimeContext],
    asset_id: str,
) -> dict[str, Any]:
    """Analyze a single image asset and return optimization insights."""

    runtime = _ctx(ctx)
    asset_row = await _get_asset_row(runtime, asset_id)
    if asset_row.get("type") != "image":
        raise MCPError("Media intelligence analysis is only available for image assets.")
    analysis = await _analyze_image_asset(runtime, asset_row)
    analysis["asset"] = asset_row
    return analysis


@mcp.tool()
async def media_scan_workspace(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """Scan all assets in a workspace and suggest optimizations."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("assets")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    scanned = 0
    not_in_avif = 0
    oversized = 0
    total_savings_bytes = 0
    top_candidates: list[dict[str, Any]] = []
    for row in rows:
        scanned += 1
        if row.get("type") == "image":
            analysis = await _analyze_image_asset(runtime, row)
            if analysis["recommended_format"] != "avif":
                not_in_avif += 1
            if analysis["is_oversized"]:
                oversized += 1
            current = int(analysis["current_size_bytes"])
            best = min(int(analysis["estimated_avif_size_bytes"]), int(analysis["estimated_webp_size_bytes"]))
            savings = max(0, current - best)
            total_savings_bytes += savings
            top_candidates.append(
                {
                    "asset_id": str(row.get("id")),
                    "name": row.get("original_filename") or row.get("id"),
                    "current_size_bytes": current,
                    "estimated_savings_bytes": savings,
                    "recommended_action": f"Convert to {analysis['recommended_format'].upper()}",
                }
            )
        else:
            top_candidates.append(
                {
                    "asset_id": str(row.get("id")),
                    "name": row.get("original_filename") or row.get("id"),
                    "current_size_bytes": int((row.get("metadata") or {}).get("size_bytes") or 0),
                    "estimated_savings_bytes": 0,
                    "recommended_action": "Video assets are already optimized by the worker pipeline.",
                }
            )
    top_five = sorted(top_candidates, key=lambda item: item["current_size_bytes"], reverse=True)[:5]
    summary = (
        f"Scanned {scanned} assets. {not_in_avif} image assets are not in AVIF yet. "
        f"{oversized} assets look oversized. Estimated total savings: {round(total_savings_bytes / (1024 * 1024), 3)} MB."
    )
    return {
        "workspace_id": workspace_id,
        "total_assets_scanned": scanned,
        "count_of_assets_not_in_avif": not_in_avif,
        "count_of_oversized_assets": oversized,
        "total_potential_savings_megabytes": round(total_savings_bytes / (1024 * 1024), 3),
        "top_five_largest_assets": top_five,
        "summary_message": summary,
    }


@mcp.tool()
async def media_find_duplicates(
    ctx: Context[ServerSession, RuntimeContext],
    workspace_id: str,
) -> dict[str, Any]:
    """Find duplicate assets in a workspace using BlurHash hamming distance."""

    runtime = _ctx(ctx)
    rows = (
        await runtime.supabase.client.table("assets")
        .select("*")
        .eq("workspace_id", workspace_id)
        .execute()
    ).data or []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        metadata = dict(row.get("metadata") or {})
        blurhash_value = metadata.get("image", {}).get("blurhash") if isinstance(metadata.get("image"), dict) else metadata.get("blurhash")
        if isinstance(blurhash_value, str) and blurhash_value:
            candidates.append(
                {
                    "asset_id": str(row.get("id")),
                    "name": row.get("original_filename") or row.get("id"),
                    "blurhash": blurhash_value,
                    "size_bytes": int(metadata.get("size_bytes") or 0),
                }
            )
    groups = _group_duplicate_candidates(candidates)
    results: list[dict[str, Any]] = []
    for group in groups:
        members = group["members"]
        if len(members) <= 1:
            continue
        representative = members[0]
        member_sizes = sorted((int(member.get("size_bytes") or 0) for member in members), reverse=True)
        wasted_bytes = sum(member_sizes[1:])
        min_distance = min(
            _hamming_distance(member["blurhash"], other["blurhash"])
            for index, member in enumerate(members)
            for other in members[index + 1 :]
        )
        max_len = max(len(member["blurhash"]) for member in members)
        similarity = round(max(0.0, (1.0 - (min_distance / max(1, max_len))) * 100.0), 2)
        results.append(
            {
                "similarity_percentage": similarity,
                "wasted_storage_bytes": wasted_bytes,
                "representative_asset": representative,
                "members": members,
            }
        )
    return {"workspace_id": workspace_id, "duplicate_groups": results, "count": len(results)}


@mcp.tool()
async def media_imagekit_migration_plan(
    ctx: Context[ServerSession, RuntimeContext],
    imagekit_private_key: str,
    imagekit_url_endpoint: str,
    folder: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Generate a full migration plan from ImageKit to StreamKit."""

    runtime = _ctx(ctx)
    files = await _list_imagekit_files(imagekit_private_key, folder=folder, page_size=max(1, min(page_size, 100)))
    migration_plan: list[dict[str, Any]] = []
    for file_row in files:
        file_path = _as_text(file_row.get("filePath") or file_row.get("path") or file_row.get("name"), "")
        if not file_path:
            continue
        source_url = _as_text(file_row.get("url") or f"{imagekit_url_endpoint.rstrip('/')}/{file_path.lstrip('/')}")
        migration_plan.append(
            {
                "source_url": source_url,
                "streamkit_url": _ik_url(_base_url(runtime.settings), file_path),
                "file_path": file_path,
                "bytes": file_row.get("size"),
                "format": file_row.get("fileType") or file_row.get("type"),
            }
        )
    file_count = len(migration_plan)
    estimated_minutes = round(max(1.0, file_count / 120.0), 2)
    instructions = [
        "1. Export the file inventory from ImageKit and verify the source paths.",
        "2. Create matching StreamKit origins or upload the files into the target workspace.",
        "3. Replace ImageKit URLs with StreamKit /ik/ or /media/ URLs generated in this plan.",
        "4. Validate a small sample first, then migrate the full file set.",
        "5. Monitor cache hits and response times after cutover.",
    ]
    return {
        "total_file_count": file_count,
        "estimated_migration_time_minutes": estimated_minutes,
        "migration_plan": migration_plan,
        "step_by_step_instructions": instructions,
    }


@mcp.tool()
async def media_convert_legacy_urls(
    ctx: Context[ServerSession, RuntimeContext],
    urls: list[str],
) -> dict[str, Any]:
    """Convert ImageKit or Cloudinary URLs to StreamKit URLs."""

    runtime = _ctx(ctx)
    converted: list[dict[str, Any]] = []
    for url in urls:
        parsed = _parse_legacy_url(url)
        host = parsed["host"]
        path = parsed["path"]
        if "imagekit.io" in host:
            transform_string = _convert_imagekit_transform(parsed.get("tr"))
            converted_url = _ik_url(_base_url(runtime.settings), path, transform_string)
            provider = "imagekit"
        elif "cloudinary.com" in host:
            segments = [segment for segment in path.split("/") if segment]
            try:
                upload_index = segments.index("upload")
            except ValueError:
                upload_index = -1
            if upload_index >= 0 and upload_index + 1 < len(segments):
                cloud_name = segments[1] if len(segments) > 1 else parsed.get("cloud_name", "cloud")
                transformations = "/".join(segments[upload_index + 1 : -1])
                asset_path = segments[-1]
                converted_url = _cloudinary_url(_base_url(runtime.settings), cloud_name, asset_path, _convert_cloudinary_transformations(transformations))
            else:
                converted_url = _cloudinary_url(_base_url(runtime.settings), parsed.get("cloud_name", "cloud"), path)
            provider = "cloudinary"
        else:
            provider = "unknown"
            converted_url = url
        converted.append({"original_url": url, "provider": provider, "streamkit_url": converted_url})
    return {"count": len(converted), "items": converted}


def main() -> None:
    """Run the MCP server."""

    mcp.run()


if __name__ == "__main__":
    main()