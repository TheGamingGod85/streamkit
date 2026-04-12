from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence
from uuid import UUID

import structlog
from celery import chord, group

from api.core.config import get_settings
from api.services.supabase_client import SupabaseRepository, SupabaseServiceError
from worker.celery_app import celery_app
from worker.tasks.metadata_task import extract_media_metadata_async
from worker.tasks.webhook_task import dispatch_workspace_webhooks
from worker.utils.ffmpeg import FFmpegError, build_hls_command, run_ffmpeg
from worker.utils.ffprobe import FFprobeError, probe_media
from worker.utils.hls import HLSVariant, build_master_playlist, get_quality_preset
from worker.utils.storage import R2StorageClient, R2StorageError

logger = structlog.get_logger(__name__)
DEFAULT_QUALITIES: tuple[str, ...] = ("360p", "480p", "720p", "1080p")


async def _transcode_quality_async(
	asset_id: UUID | str,
	source_object_key: str,
	quality: str,
) -> dict[str, Any]:
	settings = get_settings()
	storage = R2StorageClient(settings)
	supabase = await SupabaseRepository.create(settings)
	try:
		profile = get_quality_preset(quality)
		with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir_name:
			temp_dir = Path(temp_dir_name)
			source_path = temp_dir / Path(source_object_key).name
			output_dir = temp_dir / quality
			await storage.download_to_path(source_object_key, source_path)
			probe = await probe_media(source_path)

			async def _progress_callback(progress: Any) -> None:
				percent = 0.0
				if probe.duration_seconds and progress.out_time_seconds is not None:
					percent = min(100.0, max(0.0, (progress.out_time_seconds / probe.duration_seconds) * 100.0))
				logger.info(
					"transcoding_progress",
					message=f"⏳ Transcoding {quality}... {percent:.0f}% complete",
					asset_id=str(asset_id),
					quality=quality,
					percent=percent,
				)

			command = build_hls_command(
				source_path,
				output_dir,
				width=profile.width,
				height=profile.height,
				video_bitrate_kbps=max(400, profile.bandwidth // 1000),
			)
			await run_ffmpeg(command, total_duration_seconds=probe.duration_seconds, progress_callback=_progress_callback)

			segment_keys: list[str] = []
			for segment_path in sorted(output_dir.glob("segment_*.ts")):
				segment_key = f"videos/{asset_id}/{quality}/{segment_path.name}"
				await storage.upload_path(
					segment_path,
					segment_key,
					"video/mp2t",
					metadata={"asset_id": str(asset_id), "quality": quality},
				)
				segment_keys.append(segment_key)

			variant_playlist_path = output_dir / "index.m3u8"
			variant_playlist_key = f"videos/{asset_id}/{quality}/index.m3u8"
			variant_playlist_url = await storage.upload_path(
				variant_playlist_path,
				variant_playlist_key,
				"application/vnd.apple.mpegurl",
				metadata={"asset_id": str(asset_id), "quality": quality},
			)

			return {
				"asset_id": str(asset_id),
				"quality": quality,
				"status": "success",
				"width": profile.width,
				"height": profile.height,
				"bandwidth": profile.bandwidth,
				"playlist_key": variant_playlist_key,
				"playlist_url": variant_playlist_url,
				"segment_keys": segment_keys,
				"duration_seconds": probe.duration_seconds,
			}
	finally:
		await supabase.aclose()


async def _finalize_video_transcode_async(
	results: Sequence[dict[str, Any]],
	asset_id: UUID | str,
	job_id: UUID | str,
) -> dict[str, Any]:
	settings = get_settings()
	storage = R2StorageClient(settings)
	supabase = await SupabaseRepository.create(settings)
	try:
		successful_results = [r for r in results if r.get("status") == "success"]
		if not successful_results:
			logger.error("all_transcodes_failed", asset_id=str(asset_id), job_id=str(job_id))
			await supabase.update_asset_status(asset_id, "failed")
			try:
				await supabase.update_job_status(job_id, "failed", progress=100)
			except SupabaseServiceError:
				pass
			return {"asset_id": str(asset_id), "job_id": str(job_id), "status": "failed"}

		order = {quality: index for index, quality in enumerate(DEFAULT_QUALITIES)}
		ordered_results = sorted(successful_results, key=lambda result: order.get(result.get("quality", ""), 999))
		variants = [
			HLSVariant(
				name=result["quality"],
				width=int(result["width"]),
				height=int(result["height"]),
				bandwidth=int(result["bandwidth"]),
				uri=f"{result['quality']}/index.m3u8",
			)
			for result in ordered_results
		]
		master_playlist_text = build_master_playlist(variants)
		master_key = f"videos/{asset_id}/master.m3u8"
		master_url = await storage.upload_text(
			master_playlist_text,
			master_key,
			"application/vnd.apple.mpegurl",
			metadata={"asset_id": str(asset_id), "kind": "master-playlist"},
		)

		asset_row = await supabase.get_asset(asset_id)
		current_metadata = dict((asset_row or {}).get("metadata") or {})
		current_metadata.update(
			{
				"video": {
					"master_playlist_key": master_key,
					"qualities": ordered_results,
				}
			}
		)
		updated_asset = await supabase.update_asset_status(
			asset_id,
			"ready",
			metadata=current_metadata,
			master_url=master_url,
			thumbnail_url=(asset_row or {}).get("thumbnail_url"),
		)
		try:
			await supabase.update_job_status(job_id, "ready", progress=100)
		except SupabaseServiceError:
			pass
		workspace_id = str((asset_row or {}).get("workspace_id") or current_metadata.get("workspace_id") or "")
		if workspace_id:
			await dispatch_workspace_webhooks(
				settings,
				workspace_id,
				"asset.ready",
				{
					"asset_id": str(asset_id),
					"job_id": str(job_id),
					"type": "video",
					"status": "ready",
					"master_url": master_url,
					"thumbnail_url": (asset_row or {}).get("thumbnail_url"),
				},
			)

		return {
			"asset_id": str(asset_id),
			"job_id": str(job_id),
			"master_key": master_key,
			"master_url": master_url,
			"asset": updated_asset,
		}
	finally:
		await supabase.aclose()


async def process_video_asset_async(
	asset_id: UUID | str,
	job_id: UUID | str,
	source_object_key: str,
	qualities: Sequence[str] | None = None,
) -> dict[str, Any]:
	"""Run metadata extraction and schedule parallel transcode tasks."""

	await extract_media_metadata_async(asset_id, job_id, source_object_key)
	selected_qualities = tuple(qualities or DEFAULT_QUALITIES)
	workflow = chord(
		group(transcode_quality.s(str(asset_id), source_object_key, quality) for quality in selected_qualities),
		finalize_video_transcode.s(asset_id=str(asset_id), job_id=str(job_id)),
	)
	async_result = workflow.apply_async()
	return {
		"asset_id": str(asset_id),
		"job_id": str(job_id),
		"celery_group_id": async_result.id,
		"qualities": list(selected_qualities),
	}


@celery_app.task(
	name="streamkit.video.transcode_quality",
	bind=True,
	max_retries=3,
)
def transcode_quality(self, asset_id: str, source_object_key: str, quality: str) -> dict[str, Any]:
	"""Transcode one rendition of a video asset into HLS segments."""
	try:
		return asyncio.run(_transcode_quality_async(asset_id, source_object_key, quality))
	except (FFmpegError, FFprobeError, R2StorageError, SupabaseServiceError) as exc:
		if self.request.retries < self.max_retries:
			logger.warning("transcoding_retry", asset_id=asset_id, quality=quality, retries=self.request.retries, error=str(exc))
			raise self.retry(exc=exc, countdown=2 ** self.request.retries)
		logger.error("transcoding_hard_fail", asset_id=asset_id, quality=quality, error=str(exc))
		return {"asset_id": str(asset_id), "quality": quality, "status": "error", "error": str(exc)}
	except Exception as exc:
		logger.exception("transcoding_unexpected_error", asset_id=asset_id, quality=quality, error=str(exc))
		return {"asset_id": str(asset_id), "quality": quality, "status": "error", "error": str(exc)}


@celery_app.task(
	name="streamkit.video.finalize_video_transcode",
	bind=True,
	autoretry_for=(R2StorageError, SupabaseServiceError),
	retry_backoff=True,
	retry_jitter=True,
	max_retries=3,
)
def finalize_video_transcode(
	self,
	results: Sequence[dict[str, Any]],
	asset_id: str,
	job_id: str,
) -> dict[str, Any]:
	"""Build the master playlist and mark the video asset ready."""

	return asyncio.run(_finalize_video_transcode_async(results, asset_id, job_id))


async def _mark_failed_static(asset_id: str, job_id: str) -> None:
	settings = get_settings()
	supabase = await SupabaseRepository.create(settings)
	try:
		await supabase.update_asset_status(asset_id, "failed")
		await supabase.update_job_status(job_id, "failed", progress=100)
	finally:
		await supabase.aclose()


@celery_app.task(
	name="streamkit.video.process_video_asset",
	bind=True,
	max_retries=3,
)
def process_video_asset(
	self,
	asset_id: str,
	job_id: str,
	source_object_key: str,
	qualities: Sequence[str] | None = None,
) -> dict[str, Any]:
	"""Kick off the video metadata and parallel transcoding workflow."""

	try:
		return asyncio.run(process_video_asset_async(asset_id, job_id, source_object_key, qualities))
	except (FFmpegError, FFprobeError, R2StorageError, SupabaseServiceError) as exc:
		if self.request.retries < self.max_retries:
			logger.warning("process_video_retry", asset_id=asset_id, retries=self.request.retries, error=str(exc))
			raise self.retry(exc=exc, countdown=2 ** self.request.retries)
		
		# Exhausted retries, mark as failed
		logger.error("process_video_hard_fail", asset_id=asset_id, error=str(exc))
		try:
			asyncio.run(_mark_failed_static(asset_id, job_id))
		except Exception as e:
			logger.error("failed_to_update_supabase_status", asset_id=asset_id, error=str(e))
		return {"asset_id": str(asset_id), "status": "error", "error": str(exc)}
	except Exception as exc:
		logger.exception("process_video_unexpected_error", asset_id=asset_id, error=str(exc))
		try:
			asyncio.run(_mark_failed_static(asset_id, job_id))
		except Exception as e:
			logger.error("failed_to_update_supabase_status", asset_id=asset_id, error=str(e))
		return {"asset_id": str(asset_id), "status": "error", "error": str(exc)}


def enqueue_video_processing(
	asset_id: UUID | str,
	job_id: UUID | str,
	source_object_key: str,
	qualities: Sequence[str] | None = None,
) -> str:
	"""Enqueue the video workflow and return the Celery task id."""

	async_result = process_video_asset.delay(str(asset_id), str(job_id), source_object_key, list(qualities) if qualities is not None else None)
	return async_result.id
