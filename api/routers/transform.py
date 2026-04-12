from __future__ import annotations

import asyncio
from pathlib import Path
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from api.core.auth import AuthContext, get_optional_auth_context, require_asset_access
from api.core.config import Settings, get_settings
from api.models.asset import Asset, TransformParams
from api.services.r2 import R2ServiceError
from api.services.supabase_client import SupabaseServiceError
from worker.tasks.image_task import ImageTransformError, transform_image_payload

router = APIRouter(tags=["transform"])


def _get_settings(request: Request) -> Settings:
	settings = getattr(request.app.state, "settings", None)
	if isinstance(settings, Settings):
		return settings
	return get_settings()


def _get_r2_service(request: Request):
	service = getattr(request.app.state, "r2_service", None)
	if service is None or not hasattr(service, "download_bytes"):
		raise HTTPException(status_code=500, detail="R2 service is not configured.")
	return service


def _get_supabase_service(request: Request):
	service = getattr(request.app.state, "supabase_service", None)
	if service is None or not hasattr(service, "get_asset"):
		raise HTTPException(status_code=500, detail="Supabase service is not configured.")
	return service


@router.get("/media/{asset_id}", response_class=Response)
@router.get("/img/{asset_id}", response_class=Response, include_in_schema=False)
async def transform_image(
	request: Request,
	asset_id: UUID,
	params: TransformParams = Depends(),
	auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> Response:
	_get_settings(request)
	r2_service = _get_r2_service(request)
	supabase_service = _get_supabase_service(request)

	try:
		asset_row = await supabase_service.get_asset(asset_id)
	except SupabaseServiceError as exc:
		raise HTTPException(status_code=502, detail=str(exc)) from exc

	if asset_row is None:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")

	asset = Asset.model_validate(asset_row)
	require_asset_access(asset_row, auth_context)
	if asset.type != "image":
		raise HTTPException(status_code=400, detail="The requested asset is not an image.")
	started_at = time.perf_counter()

	# Format Auto-Detection
	if not params.format:
		accept = request.headers.get("Accept", "")
		if "image/avif" in accept:
			params.format = "avif"
		elif "image/webp" in accept:
			params.format = "webp"

	metadata = dict(asset.metadata or {})
	object_key = metadata.get("object_key")
	if not object_key:
		raise HTTPException(status_code=500, detail="Asset metadata does not include an object key.")

	async def _record_media_event(*, served_format: str, response_time_ms: int, cache_hit: bool) -> None:
		workspace_id = asset_row.get("workspace_id") or asset.metadata.get("workspace_id")
		if not workspace_id:
			return
		try:
			await supabase_service.client.table("media_events").insert(
				{
					"workspace_id": workspace_id,
					"asset_id": str(asset_id),
					"origin_id": None,
					"event_type": "media_served",
					"format_served": served_format,
					"bytes_saved": 0,
					"response_time_ms": response_time_ms,
					"user_agent": request.headers.get("user-agent"),
					"country_code": request.headers.get("cf-ipcountry") or request.headers.get("x-country-code"),
				}
			).execute()
		except Exception:
			pass

	# R2 Caching mechanism
	import hashlib
	import json
	param_hash = hashlib.md5(json.dumps(params.model_dump(exclude_none=True), sort_keys=True).encode()).hexdigest()
	cache_ext = params.format or "jpg"
	cache_key = f"cache/{asset.id}/{param_hash}.{cache_ext}"

	try:
		# Check if cached version exists in R2
		source_bytes = await r2_service.download_bytes(cache_key)
		if source_bytes:
			await _record_media_event(
				served_format=cache_ext,
				response_time_ms=int((time.perf_counter() - started_at) * 1000),
				cache_hit=True,
			)
			response_headers = {
				"Cache-Control": "public, max-age=31536000, immutable",
				"Content-Type": f"image/{cache_ext}",
				"X-Cache": "HIT"
			}
			return Response(content=source_bytes, media_type=f"image/{cache_ext}", headers=response_headers)
	except Exception:
		pass  # Cache miss

	try:
		source_bytes = await r2_service.download_bytes(object_key)
		transformed = await asyncio.to_thread(transform_image_payload, source_bytes, params)
	except R2ServiceError as exc:
		raise HTTPException(status_code=502, detail=str(exc)) from exc
	except ImageTransformError as exc:
		raise HTTPException(status_code=400, detail=str(exc)) from exc

	# Upload back to R2 cache
	try:
		await r2_service.upload_bytes(transformed.content, cache_key, transformed.content_type)
	except Exception as exc:
		pass # Ignore caching faults

	response_headers = {
		"X-Image-Width": str(transformed.width),
		"X-Image-Height": str(transformed.height),
		"X-Image-Format": transformed.format,
		"Cache-Control": "public, max-age=31536000, immutable",
		"X-Cache": "MISS"
	}
	if asset.original_filename:
		response_headers["Content-Disposition"] = (
			f'inline; filename="{Path(asset.original_filename).stem}.{transformed.format.lower()}"'
		)

	await _record_media_event(
		served_format=transformed.format.lower(),
		response_time_ms=int((time.perf_counter() - started_at) * 1000),
		cache_hit=False,
	)
	return Response(content=transformed.content, media_type=transformed.content_type, headers=response_headers)

@router.get("/ik/{path:path}", response_class=Response)
async def ik_rewriter(request: Request, path: str):
	"""ImageKit URL compatibility rewriter"""
	# e.g., /ik/myfolder/myimage.jpg?tr=w-300,h-200,f-webp
	tr = request.query_params.get("tr", "")
	params = {}
	if tr:
		for part in tr.split(","):
			if "-" in part:
				k, v = part.split("-", 1)
				if k == "w": params["w"] = int(v)
				elif k == "h": params["h"] = int(v)
				elif k == "f": params["f"] = v
				elif k == "q": params["q"] = int(v)
				elif k == "bl": params["blur"] = float(v)
				elif k == "r": params["r"] = int(v)
				elif k == "bg": params["bg"] = v
				elif k == "c" and v == "maintain_ratio": params["fit"] = "contain"

	from urllib.parse import urlencode, urlparse
	redirect_url = f"/media/{path}"
	if params:
		redirect_url = f"{redirect_url}?{urlencode(params)}"
	from fastapi.responses import RedirectResponse
	return RedirectResponse(url=redirect_url)
