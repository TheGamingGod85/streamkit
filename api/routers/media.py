from __future__ import annotations

from html import escape
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from api.core.auth import AuthContext, get_optional_auth_context, require_asset_access
from api.core.config import Settings, get_settings
from api.models.asset import Asset
from api.services.supabase_client import SupabaseServiceError

router = APIRouter(tags=["media"])


def _get_settings(request: Request) -> Settings:
		settings = getattr(request.app.state, "settings", None)
		if isinstance(settings, Settings):
				return settings
		return get_settings()


def _get_supabase_service(request: Request):
		service = getattr(request.app.state, "supabase_service", None)
		if service is None or not hasattr(service, "get_asset"):
				raise HTTPException(status_code=500, detail="Supabase service is not configured.")
		return service


def _build_playback_payload(asset: Asset) -> dict[str, object | None]:
		metadata = dict(asset.metadata or {})
		image_metadata = dict(metadata.get("image") or {})
		if asset.type == "video":
				return {
						"kind": "video",
						"source_url": asset.master_url,
						"poster_url": asset.thumbnail_url,
						"thumbnail_url": asset.thumbnail_url,
						"manifest_url": asset.master_url,
						"can_play": asset.master_url is not None,
				}
		source_url = image_metadata.get("preview_webp_url") or asset.thumbnail_url or asset.master_url
		return {
				"kind": "image",
				"source_url": source_url,
				"poster_url": asset.thumbnail_url,
				"thumbnail_url": asset.thumbnail_url or source_url,
				"manifest_url": None,
				"can_play": source_url is not None,
		}


def _render_player_page(asset: Asset, playback: dict[str, object | None]) -> str:
		title = escape(asset.original_filename or f"Asset {asset.id}")
		status_label = escape(asset.status.title())
		kind = escape(str(playback["kind"]))
		source_url = escape(str(playback["source_url"] or ""))
		poster_url = escape(str(playback["poster_url"] or ""))
		can_play = bool(playback["can_play"])

		if asset.type == "video" and can_play:
				media_markup = (
						f'<video class="media" controls playsinline poster="{poster_url}">'
						f'<source src="{source_url}" type="application/vnd.apple.mpegurl">'
						"Your browser does not support HLS playback."
						"</video>"
				)
		elif asset.type == "image" and can_play:
				media_markup = f'<img class="media image" src="{source_url}" alt="{title}">'
		else:
				media_markup = (
						'<div class="placeholder">'
						f'<div class="placeholder-title">{status_label}</div>'
						"<p>This asset is not ready for playback yet.</p>"
						"</div>"
				)

		return f"""<!doctype html>
<html lang="en">
	<head>
		<meta charset="utf-8">
		<meta name="viewport" content="width=device-width, initial-scale=1">
		<title>{title} | StreamKit</title>
		<style>
			:root {{
				color-scheme: dark;
				--bg: #08111f;
				--bg-alt: #102033;
				--card: rgba(10, 18, 32, 0.88);
				--border: rgba(148, 163, 184, 0.18);
				--text: #f8fafc;
				--muted: #94a3b8;
				--accent: #38bdf8;
			}}
			* {{ box-sizing: border-box; }}
			body {{
				margin: 0;
				min-height: 100vh;
				font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
				color: var(--text);
				background:
					radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 35%),
					radial-gradient(circle at bottom right, rgba(14, 165, 233, 0.14), transparent 28%),
					linear-gradient(160deg, var(--bg), var(--bg-alt));
			}}
			.shell {{
				min-height: 100vh;
				display: grid;
				place-items: center;
				padding: 32px 16px;
			}}
			.card {{
				width: min(1100px, 100%);
				border: 1px solid var(--border);
				border-radius: 28px;
				background: var(--card);
				backdrop-filter: blur(18px);
				box-shadow: 0 30px 80px rgba(2, 6, 23, 0.45);
				overflow: hidden;
			}}
			.header {{
				padding: 28px 28px 0;
				display: flex;
				flex-wrap: wrap;
				gap: 12px;
				justify-content: space-between;
				align-items: baseline;
			}}
			.eyebrow {{
				text-transform: uppercase;
				letter-spacing: 0.22em;
				color: var(--accent);
				font-size: 0.76rem;
				margin: 0 0 10px;
			}}
			h1 {{ margin: 0; font-size: clamp(1.6rem, 3vw, 2.8rem); line-height: 1.05; }}
			.meta {{ color: var(--muted); font-size: 0.95rem; }}
			.panel {{ padding: 24px 28px 28px; }}
			.media-frame {{
				border: 1px solid rgba(148, 163, 184, 0.16);
				border-radius: 24px;
				overflow: hidden;
				background: rgba(2, 6, 23, 0.45);
			}}
			.media {{
				display: block;
				width: 100%;
				max-height: min(72vh, 780px);
				object-fit: contain;
				background: #000;
			}}
			.image {{ object-fit: cover; }}
			.details {{
				display: grid;
				gap: 12px;
				margin-top: 20px;
				color: var(--muted);
				font-size: 0.95rem;
			}}
			.chips {{ display: flex; flex-wrap: wrap; gap: 10px; }}
			.chip {{
				display: inline-flex;
				align-items: center;
				gap: 8px;
				padding: 8px 12px;
				border-radius: 999px;
				background: rgba(148, 163, 184, 0.08);
				border: 1px solid rgba(148, 163, 184, 0.12);
				color: var(--text);
				font-size: 0.88rem;
			}}
			.placeholder {{
				min-height: 420px;
				display: grid;
				place-items: center;
				padding: 40px;
				text-align: center;
			}}
			.placeholder-title {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 8px; }}
			a {{ color: var(--accent); text-decoration: none; }}
		</style>
	</head>
	<body>
		<main class="shell">
			<section class="card">
				<div class="header">
					<div>
						<p class="eyebrow">StreamKit Player</p>
						<h1>{title}</h1>
					</div>
					<div class="meta">{kind} · {status_label}</div>
				</div>
				<div class="panel">
					<div class="media-frame">{media_markup}</div>
					<div class="details">
						<div class="chips">
							<span class="chip">Asset ID: {escape(str(asset.id))}</span>
							<span class="chip">Status: {status_label}</span>
							<span class="chip">Type: {escape(asset.type)}</span>
						</div>
						<div>
							{f'<a href="{source_url}" target="_blank" rel="noreferrer">Open source asset</a>' if can_play else 'Source asset is not yet available.'}
						</div>
					</div>
				</div>
			</section>
		</main>
	</body>
</html>"""


async def _load_asset(
		request: Request,
		asset_id: UUID,
		auth_context: AuthContext | None,
) -> Asset:
		supabase_service = _get_supabase_service(request)
		try:
				asset_row = await supabase_service.get_asset(asset_id)
		except SupabaseServiceError as exc:
				raise HTTPException(status_code=502, detail=str(exc)) from exc

		if asset_row is None:
				raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")

		require_asset_access(asset_row, auth_context)
		return Asset.model_validate(asset_row)


@router.get("/assets/{asset_id}")
async def get_public_asset(
		request: Request,
		asset_id: UUID,
		auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> JSONResponse:
		_get_settings(request)
		asset = await _load_asset(request, asset_id, auth_context)
		playback = _build_playback_payload(asset)
		payload = {
				"asset": asset.model_dump(mode="json"),
				"playback": playback,
				"player_url": f"/player/{asset.id}",
		}
		return JSONResponse(status_code=200, content={"success": True, "data": payload, "error": None})


@router.get("/player/{asset_id}", response_class=HTMLResponse)
async def get_player_page(
		request: Request,
		asset_id: UUID,
		auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> HTMLResponse:
		_get_settings(request)
		asset = await _load_asset(request, asset_id, auth_context)
		playback = _build_playback_payload(asset)
		return HTMLResponse(content=_render_player_page(asset, playback), status_code=200)
