# StreamKit API Reference

## Base URL

- Local: http://localhost:8000
- Production: your deployed API domain

## Authentication

Use Supabase access token in the Authorization header for protected endpoints.

```http
Authorization: Bearer <supabase_access_token>
```

Auth requirements in this reference:

- Required: valid bearer token is mandatory
- Optional: token is accepted when present
- Public: no token required

## Response Shape

Most JSON endpoints follow one of these envelopes:

```json
{ "success": true, "data": { ... }, "error": null }
```

or

```json
{ "success": true, "<resource>": { ... } }
```

Image delivery endpoints return binary responses.

## Endpoints

## System

### GET /health
- Auth: Public
- Description: service health probe

Response:

```json
{
  "success": true,
  "data": { "status": "ok" },
  "error": null
}
```

## Workspace

### GET /me/workspaces
- Auth: Required
- Description: list workspaces owned by the authenticated user

### POST /me/workspaces
- Auth: Required
- Description: create org + workspace + owner membership + initial API key

Request:

```json
{
  "workspace_id": "my-workspace",
  "name": "My Workspace"
}
```

Success response fields:

- workspace
- organization
- api_key
- metadata

### POST /workspaces
- Auth: Public
- Description: create a workspace for an existing org

Request:

```json
{
  "org_id": "<organization_id>",
  "name": "Workspace Name"
}
```

### GET /workspaces/{workspace_id}
- Auth: Public
- Description: fetch workspace by ID

### POST /workspaces/{workspace_id}/api-keys
- Auth: Public
- Description: create an API key metadata entry for a workspace

Request:

```json
{
  "name": "Production Key",
  "scopes": ["read", "write"]
}
```

### GET /workspaces/{workspace_id}/api-keys
- Auth: Public
- Description: list API key metadata for a workspace

## Bootstrap

### POST /bootstrap
- Auth: Public
- Description: anonymous bootstrap flow that provisions a workspace session when needed

Request:

```json
{
  "session_id": "browser-session-id",
  "display_name": "Personal Workspace",
  "email": "dev@example.com"
}
```

## Upload and Asset Status

### POST /upload
- Auth: Optional
- Content-Type: multipart/form-data
- Description: upload image/video and enqueue processing

Form fields:

- workspace_id (string)
- file (binary)

Success: 201 Created

```json
{
  "success": true,
  "data": {
    "asset_id": "...",
    "job_id": "...",
    "status_url": "/status/<asset_id>",
    "message": "Upload stored successfully",
    "asset": { "id": "...", "type": "image", "status": "queued" },
    "job": { "id": "...", "type": "upload", "status": "queued" }
  },
  "error": null
}
```

### GET /status/{asset_id}
- Auth: Optional
- Description: get current asset state

## Media and Transforms

### GET /assets/{asset_id}
- Auth: Optional
- Description: get asset and playback payload

### GET /player/{asset_id}
- Auth: Optional
- Description: HTML player page for an asset

### GET /media/{asset_id}
- Auth: Optional
- Description: transform and serve image bytes

Alias:

- GET /img/{asset_id}

### GET /ik/{path:path}
- Auth: Public
- Description: ImageKit-style URL rewrite endpoint

## Transform Query Parameters

Supported query aliases for /media and /proxy:

- w: width (1..8192)
- h: height (1..8192)
- f: format (jpeg, jpg, png, webp, avif)
- q: quality (1..100)
- fit: cover | contain | fill | crop
- crop: smart | center | top | bottom | left | right
- blur: float >= 0
- sharp: float >= 0
- r: rotation
- flip: h | v
- bg: background hex color
- cx, cy, cw, ch: crop rectangle
- brightness, contrast, saturation: float >= 0

## Origins

### POST /origins
- Auth: Public
- Description: create a workspace origin

Request:

```json
{
  "workspace_id": "<workspace_id>",
  "name": "Main Bucket",
  "type": "s3",
  "config": {
    "bucket": "my-bucket",
    "bucket_folder": "marketing",
    "access_key": "...",
    "secret_key": "...",
    "region": "us-east-1"
  }
}
```

### GET /origins/{workspace_id}
- Auth: Public
- Description: list origins for a workspace

### PUT /origins/{origin_id}
- Auth: Public
- Description: update origin name, type, and/or config

Request (partial allowed):

```json
{
  "name": "Renamed origin",
  "config": {
    "bucket": "new-bucket",
    "bucket_folder": "new-prefix",
    "region": "us-east-1"
  }
}
```

### DELETE /origins/{origin_id}
- Auth: Public
- Description: delete an origin

### GET /proxy/{origin_id}/{path}
- Auth: Public
- Description: fetch from configured origin, transform, and cache in R2

## Presets

### POST /presets
- Auth: Public
- Description: create transform preset

### GET /presets/{workspace_id}
- Auth: Public
- Description: list presets

### GET /presets/{workspace_id}/{name}
- Auth: Public
- Description: fetch preset by name

## Webhooks

### POST /webhooks
- Auth: Public
- Description: register webhook destination

Request:

```json
{
  "workspace_id": "<workspace_id>",
  "url": "https://example.com/webhook",
  "events": ["asset.ready", "asset.failed"],
  "secret": "optional-secret",
  "is_active": true
}
```

### GET /webhooks/{workspace_id}
- Auth: Public
- Description: list webhooks

## Analytics

### POST /analytics/events
- Auth: Public
- Description: queue analytics event

Request:

```json
{
  "workspace_id": "<workspace_id>",
  "event_type": "media.request",
  "asset_id": "<optional_asset_id>",
  "metadata": {
    "source": "frontend"
  }
}
```

### GET /analytics/workspaces/{workspace_id}
- Auth: Public
- Description: last 50 analytics events for workspace

## Compatibility Routes

### GET /ik/{workspace_id}/{path}
- Auth: Public
- Description: ImageKit-compatible route

### GET /cloudinary/{cloud_name}/image/upload/{transformations}/{path}
- Auth: Public
- Description: Cloudinary-style transform route

## Error Model

Typical errors are returned as FastAPI HTTP exceptions:

```json
{
  "detail": "error description"
}
```

Common statuses:

- 400: validation or transform error
- 401: missing/invalid auth token
- 403: forbidden asset access
- 404: resource not found
- 413: upload too large
- 415: unsupported media type
- 500/502: upstream or service integration failures
