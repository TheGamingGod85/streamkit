import os
from pathlib import Path

BASE_DIR = Path(".")

# 1. SQL Migration
migration_dir = BASE_DIR / "supabase" / "migrations"
migration_dir.mkdir(parents=True, exist_ok=True)
with open(migration_dir / "20260411000000_phase2.sql", "w") as f:
    f.write("""
CREATE TABLE organizations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT, owner_user_id UUID, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE workspaces (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org_id UUID REFERENCES organizations(id), name TEXT, slug TEXT, r2_prefix TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE workspace_members (workspace_id UUID REFERENCES workspaces(id), user_id UUID, role TEXT);
CREATE TABLE api_keys (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), key_hash TEXT, name TEXT, scopes TEXT[], last_used_at TIMESTAMPTZ);
CREATE TABLE origins (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), name TEXT, type TEXT, config JSONB);
CREATE TABLE media_events (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID, asset_id UUID, origin_id UUID, event_type TEXT, format_served TEXT, bytes_saved BIGINT, response_time_ms INT, user_agent TEXT, country_code TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE presets (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), name TEXT, params JSONB);
CREATE TABLE webhooks (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), url TEXT, secret TEXT, events TEXT[], is_active BOOLEAN);
""")

# 2. Create Routers
router_dir = BASE_DIR / "api" / "routers"

with open(router_dir / "origins.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from api.models.asset import TransformParams
from typing import Dict, Any

router = APIRouter(tags=["origins"])

@router.post("/origins")
async def create_origin(request: Request, data: Dict[str, Any]):
    return {"success": True, "data": {"id": "new-origin-id"}}

@router.get("/proxy/{origin_id}/{path:path}")
async def proxy_origin(request: Request, origin_id: str, path: str, params: TransformParams = Depends()):
    # Placeholder for fetching from external storage and applying TransformParams
    return Response(content=b"mock_image_bytes", media_type="image/jpeg")
""")

with open(router_dir / "ik_compat.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

router = APIRouter(tags=["compatibility"])

@router.get("/ik/{path:path}")
async def imagekit_rewriter(request: Request, path: str):
    tr = request.query_params.get("tr", "")
    params = {}
    if tr:
        for part in tr.split(","):
            if "-" in part:
                k, v = part.split("-", 1)
                if k == "w": params["w"] = v
                elif k == "h": params["h"] = v
                elif k == "f": params["f"] = v
                elif k == "q": params["q"] = v
                elif k == "c" and v == "maintain_ratio": params["fit"] = "contain"
    redirect_url = f"/img/dummy_asset_id?{urlencode(params)}"
    return RedirectResponse(url=redirect_url)

@router.get("/cloudinary/{path:path}")
async def cloudinary_rewriter(request: Request, path: str):
    return RedirectResponse(url=f"/img/dummy_asset_id")
""")

with open(router_dir / "workspaces.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Request
from typing import Dict, Any

router = APIRouter(tags=["workspaces"])

@router.post("/workspaces")
async def create_workspace(request: Request, data: Dict[str, Any]):
    return {"success": True, "id": "workspace-123"}

@router.get("/workspaces/{id}/api-keys")
async def list_api_keys(request: Request, id: str):
    return {"success": True, "data": []}
""")

with open(router_dir / "analytics.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Request

router = APIRouter(tags=["analytics"])

@router.get("/analytics/summary")
async def get_summary(request: Request, workspace_id: str):
    return {"success": True, "data": {"total_requests": 100, "bandwidth_saved": 5000000}}

@router.get("/analytics/formats")
async def get_formats(request: Request, workspace_id: str):
    return {"success": True, "data": {"webp": 80, "avif": 20}}
""")

with open(router_dir / "presets.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Request
from typing import Dict, Any

router = APIRouter(tags=["presets"])

@router.post("/presets")
async def create_preset(request: Request, data: Dict[str, Any]):
    return {"success": True, "id": "preset-123"}
""")

with open(router_dir / "webhooks.py", "w") as f:
    f.write("""
from fastapi import APIRouter, Request
from typing import Dict, Any

router = APIRouter(tags=["webhooks"])

@router.post("/webhooks")
async def create_webhook(request: Request, data: Dict[str, Any]):
    return {"success": True, "id": "webhook-123"}
""")

# 3. Update main.py
main_py_path = BASE_DIR / "api" / "main.py"
with open(main_py_path, "r") as f:
    main_content = f.read()

imports = """
from api.routers.origins import router as origins_router
from api.routers.ik_compat import router as ik_compat_router
from api.routers.workspaces import router as workspaces_router
from api.routers.analytics import router as analytics_router
from api.routers.presets import router as presets_router
from api.routers.webhooks import router as webhooks_router
"""

if "origins_router" not in main_content:
    main_content = main_content.replace(
        "from api.routers.media import router as media_router",
        "from api.routers.media import router as media_router\\n" + imports
    )
    
    inclusions = """
    app.include_router(origins_router)
    app.include_router(ik_compat_router)
    app.include_router(workspaces_router)
    app.include_router(analytics_router)
    app.include_router(presets_router)
    app.include_router(webhooks_router)
"""
    main_content = main_content.replace("app.include_router(media_router)", "app.include_router(media_router)\\n" + inclusions)

    with open(main_py_path, "w") as f:
        f.write(main_content)

print("Scaffolding complete.")
