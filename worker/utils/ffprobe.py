from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FFprobeError(RuntimeError):
	"""Raised when ffprobe fails to analyze a media file."""


@dataclass(frozen=True)
class MediaProbe:
	"""Structured ffprobe output for a media file."""

	duration_seconds: float | None
	width: int | None
	height: int | None
	video_codec: str | None
	audio_codec: str | None
	raw: dict[str, Any]


async def probe_media(source_path: str | Path) -> MediaProbe:
	"""Run ffprobe and return normalized media metadata."""

	command = [
		"ffprobe",
		"-v",
		"error",
		"-print_format",
		"json",
		"-show_format",
		"-show_streams",
		str(source_path),
	]
	process = await asyncio.create_subprocess_exec(
		*command,
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.PIPE,
	)
	stdout, stderr = await process.communicate()
	if process.returncode != 0:
		raise FFprobeError(stderr.decode("utf-8", errors="replace") or "ffprobe failed.")

	try:
		payload = json.loads(stdout.decode("utf-8"))
	except json.JSONDecodeError as exc:
		raise FFprobeError(f"ffprobe returned invalid JSON: {exc}") from exc

	streams = payload.get("streams", [])
	format_data = payload.get("format", {})
	video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
	audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})

	duration_value = format_data.get("duration") or video_stream.get("duration") or audio_stream.get("duration")
	duration_seconds = float(duration_value) if duration_value is not None else None

	width = int(video_stream["width"]) if video_stream.get("width") is not None else None
	height = int(video_stream["height"]) if video_stream.get("height") is not None else None

	return MediaProbe(
		duration_seconds=duration_seconds,
		width=width,
		height=height,
		video_codec=video_stream.get("codec_name"),
		audio_codec=audio_stream.get("codec_name"),
		raw=payload,
	)
