# **StreamKit**

The open source self hosted alternative to ImageKit and Cloudinary. Real time image transforms, adaptive video streaming, digital asset management, and an MCP server. All on your own infrastructure. Free forever.

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Build](https://img.shields.io/github/actions/workflow/status/streamkit/streamkit/ci.yml?label=build)
![GitHub Stars](https://img.shields.io/github/stars/streamkit/streamkit?style=social)
![Docker Pulls](https://img.shields.io/docker/pulls/streamkit/streamkit)

ImageKit: $500/month | Cloudinary: $1500/month | StreamKit: $0/month

## Table of Contents

- [What is StreamKit](#what-is-streamkit)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [API Overview](#api-overview)
- [Zero Migration Guide](#zero-migration-guide)
- [MCP Server](#mcp-server)
- [Comparison Table](#comparison-table)
- [Contributing](#contributing)
- [License](#license)

## What is StreamKit

StreamKit is a fully open source media processing platform that you can self host on your own server. It handles everything from image optimization and real time transformations to adaptive bitrate video streaming. It connects to your existing storage like AWS S3, Cloudflare R2, Azure Blob, or Google Cloud Storage without moving a single file. It ships with a built in digital asset management system, a multi tenant workspace system, analytics, webhooks, transformation presets, and a FastMCP server that lets AI tools like Claude and Cursor control your entire media pipeline using natural language.

## Features

1. Real time image transformations
Any image can be transformed via URL parameters including width, height, format, quality, fit mode, blur, rotation, flip, brightness, contrast, and saturation. It automatically serves AVIF or WebP based on the browser accept header.

2. Adaptive video streaming
Uploaded videos are automatically transcoded into 1080p, 720p, 480p, and 360p in parallel using FFmpeg and a master HLS playlist is generated for adaptive bitrate streaming.

3. Zero migration from ImageKit or Cloudinary
Companies can switch by only changing their domain name and all existing ImageKit and Cloudinary transformation URLs work identically with no code changes.

4. External storage connectors
Existing AWS S3, Cloudflare R2, Azure Blob, and Google Cloud Storage buckets can be connected directly and StreamKit fetches optimizes and serves from there without moving any files.

5. Multi tenant workspaces
Multiple isolated workspaces can be created for different teams clients or business units each with separate storage API keys and analytics.

6. Transformation presets
Named presets can be created once and reused everywhere and updating a preset automatically updates every URL that uses it.

7. Analytics
Every transformation request is tracked and dashboards show total requests bandwidth saved cache hit rate format breakdown top assets and error reports per workspace.

8. MCP server
StreamKit ships with a FastMCP server that connects to Claude Cursor and Windsurf and allows controlling the entire media pipeline using natural language prompts.

## Architecture

- API layer: FastAPI
- Async task queue: Celery backed by Redis
- Image processing: Pillow with AVIF and WebP support
- Video transcoding: FFmpeg with libx264 for HLS adaptive bitrate streaming
- Database and asset state tracking: Supabase
- Storage: Cloudflare R2 or any S3 compatible storage
- MCP server: FastMCP
- Deployment: Docker with Docker Compose

## Quick Start

1. Clone repository

```bash
git clone https://github.com/streamkit/streamkit.git
cd streamkit
```

2. Copy environment file

```bash
cp .env.example .env
```

Fill in Supabase and R2 credentials.

3. Start everything

```bash
docker compose up
```

4. Upload first asset

```bash
curl -X POST http://localhost:8000/upload \
	-F "workspace_id=<workspace_id>" \
	-F "file=@./sample.jpg"
```

5. Serve transformed asset

```bash
curl "http://localhost:8000/media/<asset_id>?w=800&f=webp"
```

## API Overview

### Upload

- `POST /upload` - Upload an image or video and enqueue processing.
- `GET /status/{asset_id}` - Get processing status for an uploaded asset.

### Transform

- `GET /media/{asset_id}` - Apply real time image transformations and return transformed bytes.
- `GET /img/{asset_id}` - Alias for media transform endpoint.
- `GET /ik/{path}` - ImageKit style transformation rewrite route.
- `GET /ik/{workspace_id}/{path}` - Workspace scoped ImageKit compatibility route.
- `GET /cloudinary/{cloud_name}/image/upload/{transformations}/{path}` - Cloudinary style compatibility route.

### Assets

- `GET /assets/{asset_id}` - Fetch an asset plus playback payload.
- `GET /player/{asset_id}` - Render embeddable player page for image/video playback.

### Origins

- `POST /origins` - Register a new origin (S3/R2/HTTP/custom).
- `GET /origins/{workspace_id}` - List origins in a workspace.
- `PUT /origins/{origin_id}` - Update origin configuration.
- `DELETE /origins/{origin_id}` - Delete an origin.
- `GET /proxy/{origin_id}/{path}` - Fetch and optimize media from a registered origin.

### Workspaces

- `GET /me/workspaces` - List workspaces owned by the authenticated user.
- `POST /me/workspaces` - Create workspace for authenticated user.
- `POST /workspaces` - Create workspace for an organization.
- `GET /workspaces/{workspace_id}` - Get workspace details.
- `POST /workspaces/{workspace_id}/api-keys` - Create workspace API key.
- `GET /workspaces/{workspace_id}/api-keys` - List workspace API keys.
- `POST /bootstrap` - Bootstrap anonymous session workspace.

### Analytics

- `POST /analytics/events` - Track analytics event.
- `GET /analytics/workspaces/{workspace_id}` - Fetch analytics event feed for workspace.

### Presets

- `POST /presets` - Create named transformation preset.
- `GET /presets/{workspace_id}` - List workspace presets.
- `GET /presets/{workspace_id}/{name}` - Get specific preset by name.

### Webhooks

- `POST /webhooks` - Register workspace webhook endpoint.
- `GET /webhooks/{workspace_id}` - List workspace webhooks.

## Zero Migration Guide

If you currently use ImageKit, you can switch by changing only your domain.

Before (ImageKit):

```text
https://ik.imagekit.io/demo/tr:w-800,h-600,f-webp,q-85/path/to/image.jpg
```

After (StreamKit):

```text
https://media.yourcompany.com/ik/path/to/image.jpg?tr=w-800,h-600,f-webp,q-85
```

Cloudinary migration works the same way.

Before (Cloudinary):

```text
https://res.cloudinary.com/demo/image/upload/c_fill,w_800,h_600,f_webp,q_85/path/to/image.jpg
```

After (StreamKit):

```text
https://media.yourcompany.com/cloudinary/demo/image/upload/c_fill,w_800,h_600,f_webp,q_85/path/to/image.jpg
```

Every transformation parameter works identically.

## MCP Server

The FastMCP server is located in the `streamkit_mcp` folder and provides:

- Asset management tools
- Transform tools
- Workspace tools
- Preset tools
- Analytics tools
- Origin connector tools
- Webhook tools
- Media intelligence tools

You can connect it to Claude Desktop, Cursor, and Windsurf using the `streamkit_mcp/mcp.json` config file.

Example prompts:

- "Upload ./hero.jpg to workspace marketing, create a WebP transform URL at width 1200, and show estimated AVIF savings."
- "List failed jobs in workspace ops, retry all video jobs, and register a webhook for asset.ready and transform.error events."

## Comparison Table

| Capability | StreamKit | ImageKit | Cloudinary |
|---|---|---|---|
| Price | $0/month | $500/month | $1500/month |
| Self hosted | Yes | No | No |
| Open source | Yes | No | No |
| Real time transforms | Yes | Yes | Yes |
| HLS video streaming | Yes | Limited | Yes |
| External storage connect | Yes | Yes | Yes |
| ImageKit URL compatible | Yes | Native | No |
| Cloudinary URL compatible | Yes | No | Native |
| MCP server | Yes | No | No |
| Multi tenant workspaces | Yes | Partial | Yes |
| GDPR and data residency control | Full control | Limited | Limited |
| Vendor lock in | None | High | High |

## Contributing

Contributions are welcome. StreamKit follows a fork and pull request workflow.

- Open an issue to report a bug or request a feature.
- Issues labeled good first issue are suitable for beginners.
- One feature or fix per pull request is preferred.
- The project responds to pull requests within 48 hours.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

StreamKit is licensed under the Apache License 2.0. You are free to use, modify, and distribute the software, including for commercial purposes, as long as you include the original license and give credit.

The name StreamKit is a trademark of the StreamKit contributors.

Built in public. MIT for the community. Star the repo if StreamKit saves you money.
