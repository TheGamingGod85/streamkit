# StreamKit MCP Server

This package exposes a production-oriented MCP server for StreamKit.

It provides tool groups for:

- Asset management
- Transform tools
- Workspace tools
- Preset tools
- Analytics tools
- Origin connector tools
- Webhook tools
- Media intelligence tools

## Run Locally

From the `streamkit` folder:

```powershell
uv run python -m streamkit_mcp.server
```

The server uses the local StreamKit API at `http://localhost:8000` by default.
You can override the API base URL with `STREAMKIT_BASE_URL`.

## Client Configuration

Use the included [mcp.json](mcp.json) file as a starting point for Claude Desktop, Cursor, and Windsurf.

Fill in the empty environment variables before starting the client.

### Claude Desktop

Add the `streamkit` server entry from `mcp.json` to your Claude Desktop MCP configuration.

### Cursor

Point Cursor at the same `mcp.json` server definition or paste the same command and environment block into Cursor's MCP settings.

### Windsurf

Use the same `command`, `args`, and `env` values from `mcp.json` in Windsurf's MCP configuration.

## Environment Variables

The server reads configuration from environment variables and the local `.env` file when present.

Required or commonly used variables:

- `STREAMKIT_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_PUBLIC_URL`
- `REDIS_URL`
- `IMAGEKIT_PRIVATE_KEY`
- `IMAGEKIT_URL_ENDPOINT`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

## Tool Groups and Example Prompts

### 1. Asset Management

Use these prompts:

- "Upload `./photo.jpg` into workspace `marketing` and return the asset and job status."
- "Show me the processing status for asset `...` and list its failed jobs if any."
- "List the newest 20 image assets in workspace `...` filtered to ready only."
- "Delete asset `...` and all of its derived files from storage."
- "Retry the failed job `...`."

### 2. Transform Tools

Use these prompts:

- "Generate a 1200px wide WebP transform URL for asset `...`."
- "Apply a real-time image transform for asset `...` with width 800, height 600, crop cover, and quality 85."
- "Generate an ImageKit-compatible URL for this asset using `tr=w-300,h-200,f-webp`."
- "Generate a Cloudinary-compatible URL for this asset using `c_fill,w_400,h_400`."

### 3. Workspace Tools

Use these prompts:

- "Create a workspace called `Acme Marketing` with the workspace ID `acme-marketing`."
- "Show the details for workspace `...`."
- "Create a workspace API key with scopes read, write, and transform."
- "List all API keys for workspace `...` without showing the secret value."

### 4. Preset Tools

Use these prompts:

- "Create a preset named `thumbnail` with WebP 800px-wide settings."
- "List all presets in workspace `...`."
- "Apply the `thumbnail` preset to asset `...` and return the final URL."

### 5. Analytics Tools

Use these prompts:

- "Give me a workspace analytics summary for workspace `...`."
- "Show the top 10 most requested assets in workspace `...`."
- "Show all transformation errors grouped by asset for workspace `...`."

### 6. Origin Connector Tools

Use these prompts:

- "Register an S3 origin for workspace `...` using bucket `my-bucket` and folder `marketing/`."
- "Register an R2 origin with path-style access and sample paths `hero.jpg` and `banner.png`."
- "Generate a proxy URL for origin `...` and asset path `products/shoe-1.jpg`."
- "Test the origin `...` using the sample path `hero.jpg` and report latency."

### 7. Webhook Tools

Use these prompts:

- "Register a webhook for workspace `...` to receive asset ready and asset failed events."
- "List all webhooks registered for workspace `...`."

### 8. Media Intelligence Tools

Use these prompts:

- "Analyze asset `...` and tell me the estimated AVIF and WebP savings."
- "Scan workspace `...` and suggest the top image optimizations."
- "Find duplicate assets in workspace `...` using BlurHash similarity."
- "Generate a migration plan from ImageKit to StreamKit using this private key and URL endpoint."
- "Convert these ImageKit and Cloudinary URLs to StreamKit URLs: ..."

## Notes

- This server is designed to work with the StreamKit API that is already running locally.
- It uses `httpx` for StreamKit API calls and the Supabase Python client for direct database access where the API does not expose a suitable endpoint.
- Do not commit real credentials into `mcp.json` or your client configuration.
