import hashlib
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.core.auth import AuthContext, get_required_auth_context
from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    workspace_id: str = Field(min_length=3, max_length=64)
    name: str


class APIKeyCreate(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64]


@router.get("/me/workspaces")
async def list_my_workspaces(
    request: Request,
    auth_context: AuthContext = Depends(get_required_auth_context),
):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        orgs = (
            await supabase.client.table("organizations")
            .select("id, name, created_at")
            .eq("owner_user_id", str(auth_context.user_id))
            .order("created_at", desc=True)
            .execute()
        )
        org_rows = orgs.data or []
        workspaces: list[dict[str, object]] = []
        for org in org_rows:
            ws = (
                await supabase.client.table("workspaces")
                .select("id, org_id, name, slug, r2_prefix, created_at")
                .eq("org_id", org["id"])
                .order("created_at", desc=True)
                .execute()
            )
            for workspace in ws.data or []:
                workspaces.append(
                    {
                        "workspace": workspace,
                        "organization": org,
                    }
                )
        return {"success": True, "workspaces": workspaces}
    finally:
        await supabase.aclose()


@router.post("/workspaces")
async def create_workspace(request: Request, data: WorkspaceCreate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        org = (
            await supabase.client.table("organizations")
            .select("id")
            .eq("id", data.org_id)
            .limit(1)
            .execute()
        )
        if not org.data:
            raise HTTPException(400, f"Organization not found for org_id={data.org_id}")

        response = await supabase.client.table("workspaces").insert({"org_id": data.org_id, "name": data.name}).execute()
        return {"success": True, "workspace": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.post("/me/workspaces")
async def create_my_workspace(
    request: Request,
    data: WorkspaceCreate,
    auth_context: AuthContext = Depends(get_required_auth_context),
):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        workspace_slug = _slugify(data.workspace_id or data.name)
        if not workspace_slug:
            raise HTTPException(400, "Workspace ID is required")

        existing = (
            await supabase.client.table("workspaces")
            .select("id")
            .eq("slug", workspace_slug)
            .limit(1)
            .execute()
        )
        if existing.data:
            raise HTTPException(409, f"Workspace ID '{workspace_slug}' already exists")

        org_row = (
            await supabase.client.table("organizations")
            .insert({"name": data.name, "owner_user_id": str(auth_context.user_id)})
            .execute()
        )
        org = org_row.data[0]

        workspace_row = (
            await supabase.client.table("workspaces")
            .insert(
                {
                    "org_id": org["id"],
                    "name": data.name,
                    "slug": workspace_slug,
                    "r2_prefix": f"workspaces/{workspace_slug}",
                }
            )
            .execute()
        )
        workspace = workspace_row.data[0]

        await supabase.client.table("workspace_members").insert(
            {
                "workspace_id": workspace["id"],
                "user_id": str(auth_context.user_id),
                "role": "owner",
            }
        ).execute()

        raw_key = "sk_test_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        key_row = (
            await supabase.client.table("api_keys")
            .insert(
                {
                    "workspace_id": workspace["id"],
                    "key_hash": key_hash,
                    "name": f"{data.name} API Key",
                    "scopes": ["read", "write"],
                }
            )
            .execute()
        )

        return {
            "success": True,
            "workspace": workspace,
            "organization": org,
            "api_key": raw_key,
            "metadata": key_row.data[0],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.get("/workspaces/{workspace_id}")
async def get_workspace(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("workspaces").select("*").eq("id", workspace_id).execute()
        if not response.data:
            raise HTTPException(404, "Workspace not found")
        return {"success": True, "workspace": response.data[0]}
    finally:
        await supabase.aclose()


@router.post("/workspaces/{workspace_id}/api-keys")
async def create_api_key(request: Request, workspace_id: str, data: APIKeyCreate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        raw_key = "sk_test_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        res = await supabase.client.table("api_keys").insert(
            {"workspace_id": workspace_id, "name": data.name, "key_hash": key_hash, "scopes": data.scopes}
        ).execute()
        return {"success": True, "api_key": raw_key, "metadata": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.get("/workspaces/{workspace_id}/api-keys")
async def list_api_keys(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = (
            await supabase.client.table("api_keys")
            .select("id, name, scopes, created_at")
            .eq("workspace_id", workspace_id)
            .execute()
        )
        return {"success": True, "api_keys": response.data}
    finally:
        await supabase.aclose()
