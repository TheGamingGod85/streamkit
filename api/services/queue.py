from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import RedisError

from api.core.config import Settings

STREAM_NAME = "streamkit:jobs"


class QueuePublisherError(RuntimeError):
    """Raised when a Redis Streams publish operation fails."""


class QueuePublisher:
    """Publish StreamKit job messages to Redis Streams."""

    def __init__(self, client: redis.Redis, stream_name: str = STREAM_NAME) -> None:
        self._client = client
        self._stream_name = stream_name

    @classmethod
    async def create(cls, settings: Settings) -> "QueuePublisher":
        """Create and validate a Redis Streams publisher from application settings."""

        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.ping()
        except RedisError as exc:
            await client.aclose()
            raise QueuePublisherError(f"Redis connection failed for '{settings.redis_url}': {exc}") from exc
        return cls(client)

    async def publish_job(
        self,
        *,
        asset_id: UUID | str,
        job_id: UUID | str,
        job_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        """Publish a job event and return the Redis Stream entry id."""

        message = {
            "asset_id": str(asset_id),
            "job_id": str(job_id),
            "job_type": job_type,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "payload_json": json.dumps(dict(payload or {}), default=str, separators=(",", ":")),
        }
        try:
            entry_id = await self._client.xadd(self._stream_name, message)
        except RedisError as exc:
            raise QueuePublisherError(
                f"Redis Streams publish failed for asset '{asset_id}' and job '{job_id}': {exc}"
            ) from exc
        return str(entry_id)

    async def aclose(self) -> None:
        """Close the underlying Redis client."""

        await self._client.aclose()
