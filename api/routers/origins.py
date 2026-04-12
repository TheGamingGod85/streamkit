import asyncio
import hashlib
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from api.models.asset import TransformParams
from api.services.supabase_client import SupabaseRepository
from worker.tasks.image_task import ImageTransformError, transform_image_payload

router = APIRouter(tags=["origins"])


class OriginCreate(BaseModel):
    workspace_id: str
    name: str
    type: str
    config: dict


class OriginUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None


def _get_r2_service(request: Request):
    return request.app.state.r2_service


@router.post("/origins")
async def create_origin(request: Request, data: OriginCreate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("origins").insert(
            {
                "workspace_id": data.workspace_id,
                "name": data.name,
                "type": data.type,
                "config": data.config,
            }
        ).execute()
        return {"success": True, "data": response.data[0]}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.get("/origins/{workspace_id}")
async def list_origins(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = (
            await supabase.client.table("origins")
            .select("*")
            .eq("workspace_id", workspace_id)
            .execute()
        )
        return {"success": True, "origins": response.data or []}
    finally:
        await supabase.aclose()


@router.put("/origins/{origin_id}")
async def update_origin(request: Request, origin_id: str, data: OriginUpdate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    payload = data.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "No fields provided for update")
    try:
        response = (
            await supabase.client.table("origins")
            .update(payload)
            .eq("id", origin_id)
            .execute()
        )
        updated = response.data[0] if response.data else None
        if not updated:
            raise HTTPException(404, "Origin not found")
        return {"success": True, "data": updated}
    finally:
        await supabase.aclose()


@router.delete("/origins/{origin_id}")
async def delete_origin(request: Request, origin_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = (
            await supabase.client.table("origins")
            .delete()
            .eq("id", origin_id)
            .execute()
        )
        deleted = response.data[0] if response.data else None
        if not deleted:
            raise HTTPException(404, "Origin not found")
        return {"success": True, "data": deleted}
    finally:
        await supabase.aclose()


@router.get("/proxy/{origin_id}/{path:path}")
async def proxy_origin(request: Request, origin_id: str, path: str, params: TransformParams = Depends()):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        res = await supabase.client.table("origins").select("*").eq("id", origin_id).single().execute()
        origin = res.data
    except Exception:
        await supabase.aclose()
        raise HTTPException(404, "Origin not found")
    await supabase.aclose()

    source_bytes = None
    if origin["type"] == "http":
        base_url = origin["config"].get("base_url", "").rstrip("/")
        url = f"{base_url}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, "Failed to fetch from origin")
            source_bytes = resp.content
    elif origin["type"] == "s3":
        bucket = origin["config"].get("bucket") or origin["config"].get("bucket_name")
        bucket_folder = (origin["config"].get("bucket_folder") or "").strip("/")
        object_key = f"{bucket_folder}/{path}".strip("/") if bucket_folder else path
        region = origin["config"].get("region", "us-east-1")
        access_key = origin["config"].get("access_key")
        secret_key = origin["config"].get("secret_key")
        endpoint = origin["config"].get("endpoint")
        path_style_access = bool(origin["config"].get("path_style_access", False))
        try:
            import boto3
            from botocore.config import Config

            kwargs = {
                "region_name": region,
                "aws_access_key_id": access_key,
                "aws_secret_access_key": secret_key,
            }
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            if path_style_access:
                kwargs["config"] = Config(s3={"addressing_style": "path"})

            s3_client = boto3.client("s3", **kwargs)
            response = s3_client.get_object(Bucket=bucket, Key=object_key)
            source_bytes = response["Body"].read()
        except Exception as e:
            raise HTTPException(500, f"S3 fetch failed: {str(e)}")
    else:
        base_url = origin["config"].get("source_url") or origin["config"].get("base_url")
        if not base_url:
            raise HTTPException(400, "Custom origins need a source_url or base_url in config")
        url = f"{str(base_url).rstrip('/')}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, "Failed to fetch from custom origin")
            source_bytes = resp.content

    if not params.format:
        accept = request.headers.get("Accept", "")
        if "image/avif" in accept:
            params.format = "avif"
        elif "image/webp" in accept:
            params.format = "webp"

    param_hash = hashlib.md5(json.dumps(params.model_dump(exclude_none=True), sort_keys=True).encode()).hexdigest()
    path_hash = hashlib.md5(path.encode()).hexdigest()
    cache_ext = params.format or "jpg"
    cache_key = f"cache/proxy/{origin_id}/{path_hash}/{param_hash}.{cache_ext}"

    r2_service = _get_r2_service(request)
    try:
        cached_bytes = await r2_service.download_bytes(cache_key)
        if cached_bytes:
            return Response(
                content=cached_bytes,
                media_type=f"image/{cache_ext}",
                headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Cache": "HIT"},
            )
    except Exception:
        pass

    try:
        transformed = await asyncio.to_thread(transform_image_payload, source_bytes, params)
    except ImageTransformError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        await r2_service.upload_bytes(transformed.content, cache_key, transformed.content_type)
    except Exception:
        pass

    return Response(
        content=transformed.content,
        media_type=transformed.content_type,
        headers={
            "X-Image-Width": str(transformed.width),
            "X-Image-Height": str(transformed.height),
            "X-Image-Format": transformed.format,
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Cache": "MISS",
        },
    )
