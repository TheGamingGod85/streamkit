from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["presets"])


class PresetCreate(BaseModel):
    workspace_id: str
    name: str
    transformations: dict


@router.post("/presets")
async def create_preset(request: Request, data: PresetCreate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("presets").insert(
            {
                "workspace_id": data.workspace_id,
                "name": data.name,
                "transformations": data.transformations,
            }
        ).execute()
        return {"success": True, "preset": response.data[0]}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.get("/presets/{workspace_id}")
async def list_presets(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("presets").select("*").eq("workspace_id", workspace_id).execute()
        return {"success": True, "presets": response.data}
    finally:
        await supabase.aclose()


@router.get("/presets/{workspace_id}/{name}")
async def get_preset(request: Request, workspace_id: str, name: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = (
            await supabase.client.table("presets")
            .select("*")
            .eq("workspace_id", workspace_id)
            .eq("name", name)
            .single()
            .execute()
        )
        return {"success": True, "preset": response.data}
    except Exception:
        raise HTTPException(404, "Preset not found")
    finally:
        await supabase.aclose()
