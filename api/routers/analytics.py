from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field

from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["analytics"])


class AnalyticsEvent(BaseModel):
    workspace_id: str
    event_type: str
    asset_id: str | None = None
    metadata: dict = Field(default_factory=dict)


async def _record_event(settings, workspace_id: str, event_type: str, asset_id: str | None, metadata: dict):
    supabase = await SupabaseRepository.create(settings)
    try:
        await supabase.client.table("analytics").insert(
            {
                "workspace_id": workspace_id,
                "event_type": event_type,
                "asset_id": asset_id,
                "metadata": metadata,
            }
        ).execute()
    finally:
        await supabase.aclose()


@router.post("/analytics/events")
async def track_event(request: Request, event: AnalyticsEvent, background_tasks: BackgroundTasks):
    settings = request.app.state.settings
    background_tasks.add_task(
        _record_event,
        settings,
        event.workspace_id,
        event.event_type,
        event.asset_id,
        event.metadata,
    )
    return {"success": True, "message": "Event queued"}


@router.get("/analytics/workspaces/{workspace_id}")
async def get_analytics(request: Request, workspace_id: str):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        response = (
            await supabase.client.table("analytics")
            .select("*")
            .eq("workspace_id", workspace_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return {"success": True, "events": response.data}
    finally:
        await supabase.aclose()
