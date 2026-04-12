from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["webhooks"])


class WebhookCreate(BaseModel):
    workspace_id: str
    url: str
    events: list[str]
    secret: str | None = None
    is_active: bool = True


@router.post("/webhooks")
async def create_webhook(request: Request, data: WebhookCreate):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("webhooks").insert(
            {
                "workspace_id": data.workspace_id,
                "url": data.url,
                "events": data.events,
                "secret": data.secret,
                "is_active": data.is_active,
            }
        ).execute()
        return {"success": True, "webhook": response.data[0]}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        await supabase.aclose()


@router.get("/webhooks/{workspace_id}")
async def list_webhooks(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = await supabase.client.table("webhooks").select("*").eq("workspace_id", workspace_id).execute()
        return {"success": True, "webhooks": response.data}
    finally:
        await supabase.aclose()
