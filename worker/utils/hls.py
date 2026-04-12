from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class HLSVariant:
	"""Metadata describing one HLS rendition."""

	name: str
	width: int
	height: int
	bandwidth: int
	uri: str
	codecs: str = "avc1.4d401f,mp4a.40.2"
	average_bandwidth: int | None = None


QUALITY_PRESETS: dict[str, HLSVariant] = {
	"360p": HLSVariant(name="360p", width=640, height=360, bandwidth=800_000, uri="360p/index.m3u8"),
	"480p": HLSVariant(name="480p", width=854, height=480, bandwidth=1_400_000, uri="480p/index.m3u8"),
	"720p": HLSVariant(name="720p", width=1280, height=720, bandwidth=2_800_000, uri="720p/index.m3u8"),
	"1080p": HLSVariant(name="1080p", width=1920, height=1080, bandwidth=5_000_000, uri="1080p/index.m3u8"),
}


def get_quality_preset(name: str) -> HLSVariant:
	"""Return the configured preset for a given quality name."""

	try:
		return QUALITY_PRESETS[name]
	except KeyError as exc:
		raise ValueError(f"Unsupported HLS quality '{name}'.") from exc


def build_variant_playlist(segment_names: Sequence[str], *, target_duration_seconds: int = 4) -> str:
	"""Build a media playlist for a single rendition."""

	lines = [
		"#EXTM3U",
		"#EXT-X-VERSION:3",
		f"#EXT-X-TARGETDURATION:{target_duration_seconds}",
		"#EXT-X-MEDIA-SEQUENCE:0",
		"#EXT-X-INDEPENDENT-SEGMENTS",
	]
	for segment_name in segment_names:
		lines.extend([f"#EXTINF:{float(target_duration_seconds):.3f},", segment_name])
	lines.append("#EXT-X-ENDLIST")
	return "\n".join(lines) + "\n"


def build_master_playlist(variants: Iterable[HLSVariant]) -> str:
	"""Build a master playlist referencing all renditions."""

	lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
	for variant in variants:
		average_bandwidth = variant.average_bandwidth or variant.bandwidth
		lines.append(
			"#EXT-X-STREAM-INF:"
			f"BANDWIDTH={variant.bandwidth},"
			f"AVERAGE-BANDWIDTH={average_bandwidth},"
			f"RESOLUTION={variant.width}x{variant.height},"
			f'CODECS="{variant.codecs}"'
		)
		lines.append(variant.uri)
	return "\n".join(lines) + "\n"
