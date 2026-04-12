from __future__ import annotations

from celery import Celery
from kombu import Queue

from api.core.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
	"""Create and configure the StreamKit Celery application."""

	resolved_settings = settings or get_settings()
	app = Celery(
		"streamkit",
		broker=resolved_settings.redis_url,
		backend=resolved_settings.redis_url,
		include=[
			"worker.tasks.video_task",
			"worker.tasks.metadata_task",
			"worker.tasks.image_task",
		],
	)
	app.conf.update(
		task_serializer="json",
		accept_content=["json"],
		result_serializer="json",
		timezone="UTC",
		enable_utc=True,
		task_track_started=True,
		task_acks_late=True,
		worker_prefetch_multiplier=1,
		broker_connection_retry_on_startup=True,
		task_default_queue="streamkit",
		task_queues=(Queue("streamkit"), Queue("video"), Queue("metadata"), Queue("image")),
		task_routes={
			"streamkit.video.transcode_quality": {"queue": "video"},
			"streamkit.video.finalize_video_transcode": {"queue": "video"},
			"streamkit.video.process_video_asset": {"queue": "video"},
			"streamkit.metadata.extract_media_metadata": {"queue": "metadata"},
			"streamkit.image.process_image_asset": {"queue": "image"},
		},
	)
	return app


celery_app = create_celery_app()
