from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import blurhash
import cv2
import numpy as np
import structlog
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
try:
    import pillow_avif
except ImportError:
    pass

from api.core.config import get_settings
from api.models.asset import CropMode, TransformParams
from api.services.supabase_client import SupabaseRepository, SupabaseServiceError
from worker.celery_app import celery_app
from worker.tasks.webhook_task import dispatch_workspace_webhooks
from worker.utils.storage import R2StorageClient, R2StorageError

logger = structlog.get_logger(__name__)

_RESAMPLING = getattr(Image, "Resampling", Image)
LANCZOS = _RESAMPLING.LANCZOS


class ImageTransformError(RuntimeError):
	"""Raised when an image transform cannot be completed."""


@dataclass(frozen=True)
class ImageTransformResult:
	"""Structured result for a transformed image payload."""

	content: bytes
	content_type: str
	width: int
	height: int
	format: str


_CONTENT_TYPE_BY_FORMAT: dict[str, str] = {
	"JPEG": "image/jpeg",
	"PNG": "image/png",
	"WEBP": "image/webp",
	"AVIF": "image/avif",
}

_PIL_FORMAT_BY_OUTPUT: dict[str, str] = {
	"jpg": "JPEG",
	"jpeg": "JPEG",
	"png": "PNG",
	"webp": "WEBP",
	"avif": "AVIF",
}

_CROP_CENTERING: dict[CropMode, tuple[float, float]] = {
	"center": (0.5, 0.5),
	"top": (0.5, 0.0),
	"bottom": (0.5, 1.0),
	"left": (0.0, 0.5),
	"right": (1.0, 0.5),
	"smart": (0.5, 0.5),
}


def _output_format_name(format_name: str | None, original_format: str | None) -> str:
	if format_name is not None:
		output = _PIL_FORMAT_BY_OUTPUT.get(format_name.lower())
		if output is None:
			raise ImageTransformError(f"Unsupported image format '{format_name}'.")
		return output
	if original_format:
		return original_format.upper()
	return "PNG"


def _content_type_for_format(format_name: str) -> str:
	try:
		return _CONTENT_TYPE_BY_FORMAT[format_name.upper()]
	except KeyError as exc:
		raise ImageTransformError(f"No content type mapping is available for '{format_name}'.") from exc


def _resize_image(image: Image.Image, width: int | None, height: int | None) -> Image.Image:
	source_width, source_height = image.size
	if width is None and height is None:
		return image.copy()
	if width is None:
		scale = height / source_height if source_height else 1.0
	elif height is None:
		scale = width / source_width if source_width else 1.0
	else:
		scale = min(
			width / source_width if source_width else 1.0,
			height / source_height if source_height else 1.0,
		)
	if scale <= 0:
		raise ImageTransformError("Requested image size must be greater than zero.")
	resized_size = (max(1, int(round(source_width * scale))), max(1, int(round(source_height * scale))))
	return image.resize(resized_size, resample=LANCZOS)


def _crop_box_for_aspect(
	source_width: int,
	source_height: int,
	target_width: int,
	target_height: int,
	center_x: int,
	center_y: int,
) -> tuple[int, int, int, int]:
	target_ratio = target_width / target_height
	source_ratio = source_width / source_height
	if source_ratio > target_ratio:
		crop_height = source_height
		crop_width = max(1, int(round(source_height * target_ratio)))
	else:
		crop_width = source_width
		crop_height = max(1, int(round(source_width / target_ratio)))
	left = max(0, min(center_x - crop_width // 2, source_width - crop_width))
	top = max(0, min(center_y - crop_height // 2, source_height - crop_height))
	return left, top, left + crop_width, top + crop_height


def _create_saliency_detector() -> Any | None:
	saliency_module = getattr(cv2, "saliency", None)
	if saliency_module is None:
		return None
	for factory_name in ("StaticSaliencySpectralResidual_create", "StaticSaliencyFineGrained_create"):
		factory = getattr(saliency_module, factory_name, None)
		if callable(factory):
			try:
				return factory()
			except Exception:
				continue
	return None


def _compute_saliency_map(detector: Any, source_bgr: np.ndarray) -> np.ndarray | None:
	saliency_map = np.zeros((source_bgr.shape[0], source_bgr.shape[1]), dtype=np.float32)
	try:
		result = detector.computeSaliency(source_bgr, saliency_map)
	except TypeError:
		try:
			result = detector.computeSaliency(source_bgr)
		except Exception:
			return None
	except Exception:
		return None

	if isinstance(result, tuple):
		if len(result) != 2:
			return None
		success, saliency_output = result
		if not success:
			return None
		saliency_map = np.asarray(saliency_output, dtype=np.float32)
	elif result is False:
		return None
	elif isinstance(result, np.ndarray):
		saliency_map = np.asarray(result, dtype=np.float32)

	if saliency_map.size == 0:
		return None
	return saliency_map


def _smart_crop_image(image: Image.Image, target_width: int, target_height: int) -> Image.Image:
	"""Crop around the saliency centroid and resize to the requested dimensions."""

	source_rgb = np.asarray(image.convert("RGB"))
	detector = _create_saliency_detector()
	if detector is None:
		return ImageOps.fit(image, (target_width, target_height), method=LANCZOS, centering=(0.5, 0.5))

	source_bgr = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2BGR)
	saliency_map = _compute_saliency_map(detector, source_bgr)
	if saliency_map is None:
		return ImageOps.fit(image, (target_width, target_height), method=LANCZOS, centering=(0.5, 0.5))

	saliency_map = cv2.GaussianBlur(saliency_map, (5, 5), 0)
	total_weight = float(np.sum(saliency_map))
	if total_weight <= 0.0:
		return ImageOps.fit(image, (target_width, target_height), method=LANCZOS, centering=(0.5, 0.5))

	indices_y, indices_x = np.indices(saliency_map.shape)
	center_x = int(round(float(np.sum(indices_x * saliency_map) / total_weight)))
	center_y = int(round(float(np.sum(indices_y * saliency_map) / total_weight)))
	left, top, right, bottom = _crop_box_for_aspect(
		image.width,
		image.height,
		target_width,
		target_height,
		center_x,
		center_y,
	)
	return image.crop((left, top, right, bottom)).resize((target_width, target_height), resample=LANCZOS)


def generate_blurhash(image: Image.Image) -> str:
	"""Generate a BlurHash string from a Pillow image."""

	rgb_image = image.convert("RGB")
	image_array = np.asarray(rgb_image)
	return blurhash.encode(image_array, components_x=4, components_y=3)


def transform_image_payload(source_bytes: bytes, params: TransformParams) -> ImageTransformResult:
	"""Transform raw image bytes according to the requested parameters."""

	with Image.open(io.BytesIO(source_bytes)) as source_image:
		original_format = source_image.format
		output_format = _output_format_name(params.format, original_format)
		transformed_image = source_image.copy()

		# 1. Manual Crop
		if all(v is not None for v in [params.crop_x, params.crop_y, params.crop_w, params.crop_h]):
			transformed_image = transformed_image.crop((
				params.crop_x,
				params.crop_y,
				params.crop_x + params.crop_w,
				params.crop_y + params.crop_h
			))

		# 2. Resize and Fit/Crop
		if params.width is not None and params.height is not None:
			if params.fit == "cover":
				transformed_image = ImageOps.fit(transformed_image, (params.width, params.height), method=LANCZOS)
			elif params.fit == "contain":
				transformed_image = ImageOps.pad(transformed_image, (params.width, params.height), method=LANCZOS, color=f"#{params.background}" if params.background else None)
			elif params.fit == "fill":
				transformed_image = transformed_image.resize((params.width, params.height), resample=LANCZOS)
			elif params.crop == "smart":
				transformed_image = _smart_crop_image(transformed_image, params.width, params.height)
			elif params.crop is not None:
				transformed_image = ImageOps.fit(
					transformed_image,
					(params.width, params.height),
					method=LANCZOS,
					centering=_CROP_CENTERING[params.crop],
				)
			else:
				transformed_image = _resize_image(transformed_image, params.width, params.height)
		elif params.width is not None or params.height is not None:
			transformed_image = _resize_image(transformed_image, params.width, params.height)

		# 3. Rotation
		if params.rotation:
			bg_color = f"#{params.background}" if params.background else None
			transformed_image = transformed_image.rotate(-params.rotation, expand=True, fillcolor=bg_color)

		# 4. Flip
		if params.flip == "h":
			transformed_image = ImageOps.mirror(transformed_image)
		elif params.flip == "v":
			transformed_image = ImageOps.flip(transformed_image)

		# 5. Adjusments
		if params.brightness is not None:
			transformed_image = ImageEnhance.Brightness(transformed_image).enhance(params.brightness)
		if params.contrast is not None:
			transformed_image = ImageEnhance.Contrast(transformed_image).enhance(params.contrast)
		if params.saturation is not None:
			transformed_image = ImageEnhance.Color(transformed_image).enhance(params.saturation)
		if params.sharp is not None:
			transformed_image = ImageEnhance.Sharpness(transformed_image).enhance(params.sharp)
		if params.blur is not None and params.blur > 0:
			transformed_image = transformed_image.filter(ImageFilter.GaussianBlur(radius=params.blur))

		save_source = transformed_image
		save_kwargs: dict[str, Any] = {}
		if output_format == "JPEG":
			save_source = transformed_image.convert("RGB")
			save_kwargs = {"quality": params.quality, "optimize": True, "progressive": True}
		elif output_format == "PNG":
			save_kwargs = {"optimize": True, "compress_level": 6}
		elif output_format == "WEBP":
			save_kwargs = {"quality": params.quality, "method": 6}
		elif output_format == "AVIF":
			save_kwargs = {"quality": params.quality}
		else:
			raise ImageTransformError(f"Unsupported image output format '{output_format}'.")

		output_buffer = io.BytesIO()
		try:
			save_source.save(output_buffer, format=output_format, **save_kwargs)
		except Exception as exc:
			if output_format == "AVIF":
				raise ImageTransformError(
					"AVIF output is not supported by the installed Pillow build."
				) from exc
			raise ImageTransformError(f"Failed to encode image as {output_format}: {exc}") from exc

		return ImageTransformResult(
			content=output_buffer.getvalue(),
			content_type=_content_type_for_format(output_format),
			width=transformed_image.width,
			height=transformed_image.height,
			format=output_format,
		)


async def process_image_asset_async(
	asset_id: UUID | str,
	job_id: UUID | str,
	source_object_key: str,
) -> dict[str, Any]:
	"""Generate optimized derivatives and blurhash metadata for an image asset."""

	settings = get_settings()
	storage = R2StorageClient(settings)
	supabase = await SupabaseRepository.create(settings)
	try:
		asset_row = await supabase.get_asset(asset_id)
		if asset_row is None:
			raise SupabaseServiceError(f"Image asset '{asset_id}' was not found.")

		await supabase.update_job_status(job_id, "processing", progress=10)
		original_bytes = await storage.download_bytes(source_object_key)
		await supabase.update_job_status(job_id, "processing", progress=25)

		with Image.open(io.BytesIO(original_bytes)) as original_image:
			original_width, original_height = original_image.size

		preview_result = await asyncio.to_thread(
			transform_image_payload,
			original_bytes,
			TransformParams(width=1600, height=1600, format="webp", quality=82),
		)
		preview_key = f"images/{asset_id}/preview.webp"
		preview_url = await storage.upload_bytes(
			preview_result.content,
			preview_key,
			preview_result.content_type,
			metadata={"asset_id": str(asset_id), "variant": "preview"},
		)

		await supabase.update_job_status(job_id, "processing", progress=50)

		smart_thumb_result = await asyncio.to_thread(
			transform_image_payload,
			original_bytes,
			TransformParams(width=512, height=512, format="webp", quality=78, crop="smart"),
		)
		smart_thumb_key = f"images/{asset_id}/smart-thumb.webp"
		smart_thumb_url = await storage.upload_bytes(
			smart_thumb_result.content,
			smart_thumb_key,
			smart_thumb_result.content_type,
			metadata={"asset_id": str(asset_id), "variant": "smart-thumb"},
		)

		await supabase.update_job_status(job_id, "processing", progress=70)

		with Image.open(io.BytesIO(smart_thumb_result.content)) as blurhash_image:
			blurhash_value = await asyncio.to_thread(generate_blurhash, blurhash_image)

		avif_url: str | None = None
		avif_error: str | None = None
		try:
			avif_result = await asyncio.to_thread(
				transform_image_payload,
				original_bytes,
				TransformParams(width=1600, height=1600, format="avif", quality=55),
			)
			avif_key = f"images/{asset_id}/preview.avif"
			avif_url = await storage.upload_bytes(
				avif_result.content,
				avif_key,
				avif_result.content_type,
				metadata={"asset_id": str(asset_id), "variant": "preview"},
			)
		except ImageTransformError as exc:
			avif_error = str(exc)

		current_metadata = dict(asset_row.get("metadata") or {})
		current_metadata.update(
			{
				"image": {
					"source_width": original_width,
					"source_height": original_height,
					"preview_webp_url": preview_url,
					"smart_thumbnail_url": smart_thumb_url,
					"preview_avif_url": avif_url,
					"avif_error": avif_error,
					"blurhash": blurhash_value,
					"preview_dimensions": {
						"width": preview_result.width,
						"height": preview_result.height,
					},
					"thumbnail_dimensions": {
						"width": smart_thumb_result.width,
						"height": smart_thumb_result.height,
					},
				}
			}
		)

		updated_asset = await supabase.update_asset_status(
			asset_id,
			"ready",
			metadata=current_metadata,
			thumbnail_url=smart_thumb_url,
		)
		await supabase.update_job_status(job_id, "ready", progress=100)
		workspace_id = str(asset_row.get("workspace_id") or current_metadata.get("workspace_id") or "")
		if workspace_id:
			await dispatch_workspace_webhooks(
				settings,
				workspace_id,
				"asset.ready",
				{
					"asset_id": str(asset_id),
					"job_id": str(job_id),
					"type": "image",
					"status": "ready",
					"thumbnail_url": smart_thumb_url,
					"master_url": None,
				},
			)
		logger.info(
			"image_asset_processed",
			asset_id=str(asset_id),
			job_id=str(job_id),
			preview_webp_url=preview_url,
			smart_thumbnail_url=smart_thumb_url,
			preview_avif_url=avif_url,
		)
		return {
			"asset_id": str(asset_id),
			"job_id": str(job_id),
			"preview_webp_url": preview_url,
			"smart_thumbnail_url": smart_thumb_url,
			"preview_avif_url": avif_url,
			"avif_error": avif_error,
			"blurhash": blurhash_value,
			"asset": updated_asset,
		}
	except Exception as exc:
		try:
			await supabase.update_asset_status(asset_id, "failed", error=str(exc))
		except Exception:
			pass
		try:
			await supabase.update_job_status(job_id, "failed", error=str(exc))
		except Exception:
			pass
		raise
	finally:
		await supabase.aclose()


@celery_app.task(
	name="streamkit.image.process_image_asset",
	bind=True,
	autoretry_for=(ImageTransformError, R2StorageError, SupabaseServiceError),
	retry_backoff=True,
	retry_jitter=True,
	max_retries=3,
)
def process_image_asset(self, asset_id: str, job_id: str, source_object_key: str) -> dict[str, Any]:
	"""Celery task wrapper for image optimization and blurhash generation."""

	return asyncio.run(process_image_asset_async(asset_id, job_id, source_object_key))


def enqueue_image_processing(asset_id: UUID | str, job_id: UUID | str, source_object_key: str) -> str:
	"""Enqueue the image optimization workflow and return the Celery task id."""

	async_result = process_image_asset.delay(str(asset_id), str(job_id), source_object_key)
	return async_result.id
