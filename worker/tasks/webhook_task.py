import hashlib
import hmac
import json
from typing import Any

import httpx
from celery import shared_task

from api.services.supabase_client import SupabaseRepository


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def fire_webhook(self, url: str, secret: str, event_name: str, payload: dict):
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"event": event_name, "data": payload})

    if secret:
        signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = signature

    try:
        response = httpx.post(url, content=body, headers=headers, timeout=10.0)
        response.raise_for_status()
        return {"status": "success", "status_code": response.status_code}
    except Exception as exc:
        raise self.retry(exc=exc)


async def dispatch_workspace_webhooks(settings, workspace_id: str, event_name: str, payload: dict[str, Any]) -> int:
    """Queue webhook deliveries for all active workspace webhooks matching the event."""

    supabase = await SupabaseRepository.create(settings)
    queued = 0
    try:
        response = (
            await supabase.client.table("webhooks")
            .select("url, secret, events, is_active")
            .eq("workspace_id", workspace_id)
            .eq("is_active", True)
            .execute()
        )
        for webhook in response.data or []:
            events = webhook.get("events") or []
            if events and event_name not in events and "*" not in events:
                continue
            fire_webhook.delay(webhook["url"], webhook.get("secret") or "", event_name, payload)
            queued += 1
        return queued
    finally:
        await supabase.aclose()
