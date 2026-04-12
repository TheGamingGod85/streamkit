from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["bootstrap"])


class BootstrapRequest(BaseModel):
    session_id: str = Field(min_length=8)
    display_name: str | None = None
    email: str | None = None


@router.post("/bootstrap")
async def bootstrap_workspace(request: Request, data: BootstrapRequest):
    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    try:
        existing = (
            await supabase.client.table("workspace_bootstrap_sessions")
            .select("session_id, display_name, email, org_id, workspace_id, api_key_id")
            .eq("session_id", data.session_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            session_row = existing.data[0]
            workspace = (
                await supabase.client.table("workspaces")
                .select("*")
                .eq("id", session_row["workspace_id"])
                .limit(1)
                .execute()
            )
            api_keys = (
                await supabase.client.table("api_keys")
                .select("id, name, scopes, created_at")
                .eq("workspace_id", session_row["workspace_id"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            try:
                await supabase.client.table("workspace_bootstrap_sessions").update(
                    {"last_seen_at": datetime.now(timezone.utc).isoformat()}
                ).eq("session_id", data.session_id).execute()
            except Exception:
                pass
            return {
                "success": True,
                "created": False,
                "bootstrap": {
                    "session_id": session_row["session_id"],
                    "workspace_id": session_row["workspace_id"],
                    "org_id": session_row["org_id"],
                    "workspace": workspace.data[0] if workspace.data else None,
                    "api_key": None,
                    "api_keys": api_keys.data or [],
                },
            }

        display_name = (data.display_name or "Personal Workspace").strip() or "Personal Workspace"
        org_name = display_name if len(display_name) <= 80 else display_name[:80]
        slug = re.sub(r"[^a-z0-9]+", "-", org_name.lower()).strip("-") or f"workspace-{secrets.token_hex(4)}"

        org_row = (
            await supabase.client.table("organizations")
            .insert({"name": org_name})
            .execute()
        )
        org = org_row.data[0]

        workspace_row = (
            await supabase.client.table("workspaces")
            .insert({"org_id": org["id"], "name": display_name, "slug": slug, "r2_prefix": f"workspaces/{slug}"})
            .execute()
        )
        workspace = workspace_row.data[0]

        raw_key = "sk_test_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        key_row = (
            await supabase.client.table("api_keys")
            .insert(
                {
                    "workspace_id": workspace["id"],
                    "key_hash": key_hash,
                    "name": "Auto-generated key",
                    "scopes": ["read", "write"],
                }
            )
            .execute()
        )
        api_key = key_row.data[0]

        try:
            await supabase.client.table("workspace_bootstrap_sessions").insert(
                {
                    "session_id": data.session_id,
                    "display_name": data.display_name,
                    "email": str(data.email) if data.email else None,
                    "org_id": org["id"],
                    "workspace_id": workspace["id"],
                    "api_key_id": api_key["id"],
                }
            ).execute()
        except Exception:
            pass

        return {
            "success": True,
            "created": True,
            "bootstrap": {
                "session_id": data.session_id,
                "workspace_id": workspace["id"],
                "org_id": org["id"],
                "workspace": workspace,
                "api_key": raw_key,
                "api_keys": [api_key],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await supabase.aclose()
