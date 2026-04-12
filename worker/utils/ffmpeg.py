from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence


class FFmpegError(RuntimeError):
	"""Raised when FFmpeg exits with a non-zero status code."""


@dataclass(frozen=True)
class FFmpegProgress:
	"""Progress information parsed from FFmpeg's -progress output."""

	out_time_seconds: float | None
	speed: float | None
	progress: str
	raw: dict[str, str]

	@property
	def is_complete(self) -> bool:
		return self.progress == "end"


async def run_ffmpeg(
	command: Sequence[str],
	*,
	total_duration_seconds: float | None = None,
	progress_callback: Callable[[FFmpegProgress], Awaitable[None] | None] | None = None,
	cwd: Path | None = None,
) -> None:
	"""Run an FFmpeg command and stream progress updates from stderr."""

	process = await asyncio.create_subprocess_exec(
		*command,
		stdout=asyncio.subprocess.DEVNULL,
		stderr=asyncio.subprocess.PIPE,
		cwd=str(cwd) if cwd is not None else None,
	)
	if process.stderr is None:
		raise FFmpegError("FFmpeg stderr stream is not available.")

	progress_state: dict[str, str] = {}
	stderr_lines: list[str] = []
	try:
		while True:
			raw_line = await process.stderr.readline()
			if not raw_line:
				break
			line = raw_line.decode("utf-8", errors="replace").strip()
			if not line:
				continue
			stderr_lines.append(line)
			if "=" not in line:
				continue
			key, value = line.split("=", 1)
			progress_state[key] = value
			if key == "progress":
				progress = _build_progress(progress_state, total_duration_seconds)
				if progress_callback is not None:
					result = progress_callback(progress)
					if inspect.isawaitable(result):
						await result
				if value == "end":
					progress_state = {}
	except Exception:
		# Ensure ffmpeg is terminated before re-raising so temporary files can be removed on Windows.
		if process.returncode is None:
			process.kill()
		await process.wait()
		raise

	return_code = await process.wait()
	if return_code != 0:
		tail = "\n".join(stderr_lines[-20:])
		raise FFmpegError(f"FFmpeg exited with code {return_code}. {tail}")


def build_hls_command(
	source_path: Path,
	output_dir: Path,
	*,
	width: int,
	height: int,
	video_bitrate_kbps: int,
	audio_bitrate_kbps: int = 128,
	segment_time_seconds: int = 4,
) -> list[str]:
	"""Build an FFmpeg command that emits HLS segments and a variant playlist."""

	output_dir.mkdir(parents=True, exist_ok=True)
	segment_pattern = output_dir / "segment_%03d.ts"
	playlist_path = output_dir / "index.m3u8"
	scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2"
	return [
		"ffmpeg",
		"-y",
		"-i",
		str(source_path),
		"-vf",
		scale_filter,
		"-c:v",
		"libx264",
		"-preset",
		"veryfast",
		"-profile:v",
		"main",
		"-pix_fmt",
		"yuv420p",
		"-b:v",
		f"{video_bitrate_kbps}k",
		"-maxrate",
		f"{int(video_bitrate_kbps * 1.1)}k",
		"-bufsize",
		f"{int(video_bitrate_kbps * 2)}k",
		"-c:a",
		"aac",
		"-b:a",
		f"{audio_bitrate_kbps}k",
		"-ac",
		"2",
		"-f",
		"hls",
		"-hls_time",
		str(segment_time_seconds),
		"-hls_playlist_type",
		"vod",
		"-hls_flags",
		"independent_segments",
		"-hls_segment_filename",
		str(segment_pattern),
		"-progress",
		"pipe:2",
		"-nostats",
		"-loglevel",
		"error",
		str(playlist_path),
	]


def build_thumbnail_command(
	source_path: Path,
	thumbnail_path: Path,
	*,
	timestamp_seconds: int = 5,
	width: int = 640,
) -> list[str]:
	"""Build an FFmpeg command that extracts a thumbnail frame."""

	thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
	return [
		"ffmpeg",
		"-y",
		"-ss",
		str(timestamp_seconds),
		"-i",
		str(source_path),
		"-frames:v",
		"1",
		"-vf",
		f"scale={width}:-1",
		"-q:v",
		"2",
		"-progress",
		"pipe:2",
		"-nostats",
		"-loglevel",
		"error",
		str(thumbnail_path),
	]


def _build_progress(progress_state: dict[str, str], total_duration_seconds: float | None) -> FFmpegProgress:
	out_time_seconds: float | None = None
	raw_out_time_ms = progress_state.get("out_time_ms")
	raw_out_time_us = progress_state.get("out_time_us")
	if raw_out_time_ms is not None:
		out_time_seconds = _parse_out_time_seconds(raw_out_time_ms)
	elif raw_out_time_us is not None:
		out_time_seconds = _parse_out_time_seconds(raw_out_time_us)

	if total_duration_seconds and out_time_seconds is not None:
		percent = min(100.0, max(0.0, (out_time_seconds / total_duration_seconds) * 100.0))
		progress_state["percent"] = f"{percent:.2f}"

	speed_value = progress_state.get("speed")
	speed: float | None = None
	if speed_value and speed_value.endswith("x"):
		try:
			speed = float(speed_value.removesuffix("x"))
		except ValueError:
			speed = None

	return FFmpegProgress(
		out_time_seconds=out_time_seconds,
		speed=speed,
		progress=progress_state.get("progress", "continue"),
		raw=dict(progress_state),
	)


def _parse_out_time_seconds(raw_value: str) -> float | None:
	"""Parse ffmpeg out_time values and tolerate sentinel values like N/A."""

	if not raw_value or raw_value.upper() == "N/A":
		return None
	try:
		return int(raw_value) / 1_000_000
	except ValueError:
		return None
