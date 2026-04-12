from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api.core.auth import AuthContext, get_optional_auth_context, require_asset_access
from api.core.config import Settings, get_settings
from api.models.asset import Asset, Job, UploadResponse
from api.services.queue import QueuePublisherError
from api.services.r2 import R2ServiceError
from api.services.supabase_client import SupabaseServiceError

router = APIRouter(tags=["upload"])

SUPPORTED_IMAGE_PREFIX = "image/"
SUPPORTED_VIDEO_PREFIX = "video/"


def _get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


def _get_r2_service(request: Request) -> Any:
    service = getattr(request.app.state, "r2_service", None)
    if service is None or not hasattr(service, "upload_file"):
        raise HTTPException(status_code=500, detail="R2 service is not configured.")
    return service


def _get_supabase_service(request: Request):
    service = getattr(request.app.state, "supabase_service", None)
    if service is None or not hasattr(service, "create_asset"):
        raise HTTPException(status_code=500, detail="Supabase service is not configured.")
    return service


def _get_queue_publisher(request: Request):
    service = getattr(request.app.state, "queue_publisher", None)
    if service is None or not hasattr(service, "publish_job"):
        raise HTTPException(status_code=500, detail="Queue publisher is not configured.")
    return service


def _media_type_to_asset_type(content_type: str) -> str:
    if content_type.startswith(SUPPORTED_IMAGE_PREFIX):
        return "image"
    if content_type.startswith(SUPPORTED_VIDEO_PREFIX):
        return "video"
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Only image and video uploads are supported.",
    )


def _build_object_key(asset_id: UUID, filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(content_type) or ".bin"
    return f"assets/{asset_id}/original{suffix}"


async def _measure_upload_size(file: UploadFile, chunk_size: int) -> int:
    size = 0
    await file.seek(0)
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        size += len(chunk)
    await file.seek(0)
    return size


@router.post("/upload")
async def upload_asset(
    request: Request,
    workspace_id: str = Form(...),
    private_asset: bool = Form(False),
    file: UploadFile = File(...),
    auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> JSONResponse:
    if private_asset and auth_context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Private uploads require authentication.")

    settings = _get_settings(request)
    r2_service = _get_r2_service(request)
    supabase_service = _get_supabase_service(request)
    queue_publisher = _get_queue_publisher(request)

    content_type = (file.content_type or "").strip().lower()
    if not content_type:
        raise HTTPException(status_code=400, detail="Missing file content type.")

    asset_type = _media_type_to_asset_type(content_type)
    filename = Path(file.filename or "upload.bin").name

    size_bytes = await _measure_upload_size(file, settings.upload_chunk_size_bytes)
    if size_bytes > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds the maximum size of {settings.max_upload_size_mb} MB.",
        )

    asset_id = uuid4()
    job_id = uuid4()
    r2_base_path = f"assets/{asset_id}"
    object_key = _build_object_key(asset_id, filename, content_type)
    metadata = {
        "content_type": content_type,
        "size_bytes": size_bytes,
        "object_key": object_key,
        "upload_filename": filename,
        "visibility": "private" if private_asset else "public",
    }

    asset_payload = {
        "id": str(asset_id),
        "user_id": str(auth_context.user_id) if private_asset and auth_context is not None else None,
        "workspace_id": workspace_id,
        "type": asset_type,
        "status": "queued",
        "original_filename": filename,
        "r2_base_path": r2_base_path,
        "metadata": metadata,
    }
    job_payload = {
        "id": str(job_id),
        "asset_id": str(asset_id),
        "workspace_id": workspace_id,
        "type": "upload",
        "status": "queued",
        "progress": 0,
        "error": None,
    }

    try:
        asset_row = await supabase_service.create_asset(asset_payload)
    except SupabaseServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        job_row = await supabase_service.create_job(job_payload)
    except SupabaseServiceError as exc:
        try:
            await supabase_service.update_asset_status(asset_id, "failed", metadata=metadata, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        await file.seek(0)
        await r2_service.upload_file(
            fileobj=file.file,
            object_key=object_key,
            content_type=content_type,
            metadata={"asset_id": str(asset_id), "filename": filename},
        )
    except R2ServiceError as exc:
        try:
            await supabase_service.update_asset_status(asset_id, "failed", metadata=metadata, error=str(exc))
        except Exception:
            pass
        try:
            await supabase_service.update_job_status(job_id, "failed", error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        await queue_publisher.publish_job(
            asset_id=asset_id,
            job_id=job_id,
            job_type="upload",
            payload={
                "asset_type": asset_type,
                "content_type": content_type,
                "object_key": object_key,
                "r2_base_path": r2_base_path,
                "original_filename": filename,
                "size_bytes": size_bytes,
                "workspace_id": workspace_id,
            },
        )
    except QueuePublisherError as exc:
        try:
            await supabase_service.update_asset_status(asset_id, "failed", metadata=metadata, error=str(exc))
        except Exception:
            pass
        try:
            await supabase_service.update_job_status(job_id, "failed", error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if asset_type == "video":
        try:
            from worker.tasks.video_task import enqueue_video_processing

            enqueue_video_processing(asset_id, job_id, object_key)
        except Exception as exc:
            try:
                await supabase_service.update_asset_status(asset_id, "failed", metadata=metadata, error=str(exc))
            except Exception:
                pass
            try:
                await supabase_service.update_job_status(job_id, "failed", error=str(exc))
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=f"Failed to enqueue video processing: {exc}") from exc
    elif asset_type == "image":
        try:
            from worker.tasks.image_task import enqueue_image_processing

            enqueue_image_processing(asset_id, job_id, object_key)
        except Exception as exc:
            try:
                await supabase_service.update_asset_status(asset_id, "failed", metadata=metadata, error=str(exc))
            except Exception:
                pass
            try:
                await supabase_service.update_job_status(job_id, "failed", error=str(exc))
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=f"Failed to enqueue image processing: {exc}") from exc

    response = UploadResponse(
        asset_id=asset_id,
        job_id=job_id,
        status_url=f"/status/{asset_id}",
        asset=Asset.model_validate(asset_row),
        job=Job.model_validate(job_row),
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"success": True, "data": response.model_dump(mode="json"), "error": None},
    )


@router.get("/status/{asset_id}")
async def get_status(
    request: Request,
    asset_id: UUID,
    auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> JSONResponse:
    supabase_service = _get_supabase_service(request)
    try:
        asset_row = await supabase_service.get_asset(asset_id)
    except SupabaseServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if asset_row is None:
        raise HTTPException(status_code=404, detail="Asset not found.")

    require_asset_access(asset_row, auth_context)

    asset = Asset.model_validate(asset_row)
    payload: dict[str, Any] = {"asset": asset.model_dump(mode="json"), "status": asset.status}
    return JSONResponse(status_code=200, content={"success": True, "data": payload, "error": None})
