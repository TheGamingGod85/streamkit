from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

import blurhash
import structlog

from api.core.config import get_settings
from api.services.supabase_client import SupabaseRepository, SupabaseServiceError
from worker.celery_app import celery_app
from worker.utils.ffmpeg import FFmpegError, build_thumbnail_command, run_ffmpeg
from worker.utils.ffprobe import FFprobeError, MediaProbe, probe_media
from worker.utils.storage import R2StorageClient, R2StorageError

logger = structlog.get_logger(__name__)


async def _extract_blurhash_frame(image_path: Path, size: int = 32) -> list[list[list[int]]]:
	"""Decode a thumbnail into a small RGB frame suitable for BlurHash encoding."""

	command = [
		"ffmpeg",
		"-y",
		"-i",
		str(image_path),
		"-vf",
		f"scale={size}:{size}:force_original_aspect_ratio=decrease,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2,format=rgb24",
		"-f",
		"rawvideo",
		"pipe:1",
	]
	process = await asyncio.create_subprocess_exec(
		*command,
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.PIPE,
	)
	stdout, stderr = await process.communicate()
	if process.returncode != 0:
		raise FFmpegError(stderr.decode("utf-8", errors="replace") or "Failed to decode thumbnail for blurhash.")

	expected_size = size * size * 3
	if len(stdout) != expected_size:
		raise FFmpegError(
			f"Unexpected blurhash frame size for '{image_path}': expected {expected_size} bytes, got {len(stdout)} bytes."
		)

	frame: list[list[list[int]]] = []
	row_width = size * 3
	for row_index in range(size):
		row: list[list[int]] = []
		row_start = row_index * row_width
		for column_index in range(size):
			pixel_start = row_start + column_index * 3
			row.append([stdout[pixel_start], stdout[pixel_start + 1], stdout[pixel_start + 2]])
		frame.append(row)
	return frame


async def extract_media_metadata_async(asset_id: UUID | str, job_id: UUID | str, source_object_key: str) -> dict[str, Any]:
	"""Extract media metadata, thumbnail, and blurhash for a video asset."""

	settings = get_settings()
	storage = R2StorageClient(settings)
	supabase = await SupabaseRepository.create(settings)
	asset_row: dict[str, Any] | None = None
	try:
		asset_row = await supabase.get_asset(asset_id)
		with TemporaryDirectory() as temp_dir_name:
			temp_dir = Path(temp_dir_name)
			source_path = temp_dir / Path(source_object_key).name
			thumbnail_path = temp_dir / f"{Path(source_object_key).stem}-thumbnail.jpg"

			await storage.download_to_path(source_object_key, source_path)
			probe = await probe_media(source_path)
			await run_ffmpeg(build_thumbnail_command(source_path, thumbnail_path, timestamp_seconds=5, width=640), total_duration_seconds=probe.duration_seconds)

			blurhash_frame = await _extract_blurhash_frame(thumbnail_path)
			blurhash_value = blurhash.encode(blurhash_frame, components_x=4, components_y=3)
			thumbnail_key = f"videos/{asset_id}/thumbnail.jpg"
			thumbnail_url = await storage.upload_path(
				thumbnail_path,
				thumbnail_key,
				"image/jpeg",
				metadata={"asset_id": str(asset_id), "kind": "thumbnail"},
			)

			current_metadata = dict((asset_row or {}).get("metadata") or {})
			current_metadata.update(
				{
					"probe": probe.raw,
					"video": {
						"duration_seconds": probe.duration_seconds,
						"width": probe.width,
						"height": probe.height,
						"video_codec": probe.video_codec,
						"audio_codec": probe.audio_codec,
					},
					"blurhash": blurhash_value,
					"thumbnail_key": thumbnail_key,
				}
			)

			updated_asset = await supabase.update_asset_status(
				asset_id,
				"processing",
				metadata=current_metadata,
				thumbnail_url=thumbnail_url,
			)
			try:
				await supabase.update_job_status(job_id, "processing", progress=15)
			except SupabaseServiceError:
				pass

			return {
				"asset_id": str(asset_id),
				"thumbnail_key": thumbnail_key,
				"thumbnail_url": thumbnail_url,
				"blurhash": blurhash_value,
				"probe": probe.raw,
				"asset": updated_asset,
			}
	finally:
		await supabase.aclose()


@celery_app.task(
	name="streamkit.metadata.extract_media_metadata",
	bind=True,
	autoretry_for=(FFmpegError, FFprobeError, R2StorageError, SupabaseServiceError),
	retry_backoff=True,
	retry_jitter=True,
	max_retries=3,
)
def extract_media_metadata(self, asset_id: str, job_id: str, source_object_key: str) -> dict[str, Any]:
	"""Celery task wrapper for extracting video metadata."""

	return asyncio.run(extract_media_metadata_async(asset_id, job_id, source_object_key))
