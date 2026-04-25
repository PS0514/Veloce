import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

# Set service name for logging before importing get_logger
os.environ["VELOCE_SERVICE_NAME"] = "gmail"

import aiohttp
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from veloce.orchestrator.gmail_client import GmailClient
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.config import load_listener_config
from veloce.runtime_config import get_config_value

logger = get_logger(__name__)
app = FastAPI(title="Veloce Gmail Service", version="0.1.0")

class GmailConfig:
    def __init__(self):
        # Load unified configuration
        l_config = load_listener_config()
        self.poll_interval = int(os.getenv("GMAIL_POLL_INTERVAL", "60"))
        
        # Priority: VELOCE_ORCHESTRATOR_URL (via load_listener_config) -> N8N_WEBHOOK_URL
        self.webhook_url = l_config.webhook_url
        self.history_days = l_config.startup_history_days or 3
        
        # Robustly derive endpoints
        if self.webhook_url:
            # Strip the specific endpoint if present to get the base
            base_url = self.webhook_url.replace("/veloce-task-scheduler", "")
            if not base_url.endswith("/"):
                base_url += "/"
            
            self.ingest_url = f"{base_url}telegram-context-ingest"
            self.ids_url = f"{base_url}gmail-context-ids"
        else:
            self.ingest_url = None
            self.ids_url = None

config = GmailConfig()
gmail_client = GmailClient()

async def post_to_orchestrator(payload: dict) -> bool:
    if not config.webhook_url:
        log_warning(logger, "gmail_webhook_url_missing")
        return False
    
    success = True
    # Forward to scheduler
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(config.webhook_url, json=payload, timeout=60) as resp:
                resp.raise_for_status()
                log_info(logger, "gmail_forwarded_to_orchestrator", msg_id=payload.get("message_id"), subject=payload.get("chat_title"))
        except Exception as e:
            log_warning(logger, "gmail_forward_failed", error=str(e))
            success = False

    # Forward to ingest (background context) - failing here shouldn't block the main success
    if config.ingest_url:
        ingest_payload = {
            "source": "gmail",
            "message_id": payload["message_id"],
            "sender_id": payload["sender_id"],
            "chat_id": payload["chat_id"],
            "chat_title": payload["chat_title"],
            "message": payload["message"],
            "date": payload["date"]
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(config.ingest_url, json=ingest_payload, timeout=60) as resp:
                    resp.raise_for_status()
            except Exception as e:
                log_warning(logger, "gmail_ingest_failed", error=str(e))
            
    return success

async def wait_for_orchestrator():
    """Wait for the orchestrator to be healthy before proceeding."""
    if not config.webhook_url:
        return
    
    from urllib.parse import urlparse
    parsed = urlparse(config.webhook_url)
    health_url = f"{parsed.scheme}://{parsed.netloc}/health"
    
    log_info(logger, "gmail_waiting_for_orchestrator", url=health_url)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(health_url, timeout=5) as resp:
                    if resp.status == 200:
                        log_info(logger, "gmail_orchestrator_ready")
                        return
            except Exception:
                pass
            await asyncio.sleep(3)

async def gmail_poll_loop():
    await wait_for_orchestrator()
    log_info(logger, "gmail_poll_loop_start", interval=config.poll_interval)
    
    # Initialize processed IDs by fetching from orchestrator
    processed_ids = set()
    if config.ids_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(config.ids_url, timeout=20) as resp:
                    if resp.status == 200:
                        id_list = await resp.json()
                        processed_ids = set(id_list)
                        log_info(logger, "gmail_sync_state_loaded", count=len(processed_ids))
        except Exception as e:
            log_warning(logger, "gmail_sync_state_load_failed", error=str(e))
    else:
        log_warning(logger, "gmail_ids_url_missing_dedup_skipped")
    
    while True:
        # Check enabled status dynamically from runtime config
        enabled = str(get_config_value("enable_gmail_sync", "true")).lower() == "true"
        if not enabled:
            await asyncio.sleep(60)
            continue
            
        try:
            # Calculate date for history scan
            start_date = datetime.now(timezone.utc) - timedelta(days=config.history_days)
            query = f"after:{start_date.strftime('%Y/%m/%d')}"
            
            log_info(logger, "gmail_polling", query=query)
            messages_summary = gmail_client.list_messages(query=query, max_results=50)
            
            new_count = 0
            for msg_summ in reversed(messages_summary): # Process oldest first
                msg_id = msg_summ["id"]
                if msg_id in processed_ids:
                    continue
                
                try:
                    full_msg = gmail_client.get_message(msg_id)
                    parsed = gmail_client.parse_message(full_msg)
                    
                    # SKIP MARKETING/NOREPLY
                    sender_lower = parsed["sender"].lower()
                    if any(x in sender_lower for x in ["noreply", "no-reply", "marketing", "newsletter", "shein", "shopee", "lazada", "deals", "offer", "promotion"]):
                        log_info(logger, "gmail_skip_marketing", msg_id=msg_id, sender=parsed["sender"])
                        processed_ids.add(msg_id)
                        continue

                    payload = {
                        "source": "gmail",
                        "message_id": parsed["id"],
                        "sender_id": parsed["sender"],
                        "chat_id": "gmail_inbox",
                        "chat_title": f"Gmail: {parsed['subject']}",
                        "message": f"Subject: {parsed['subject']}\nFrom: {parsed['sender']}\n\n{parsed['body']}",
                        "date": parsed["date"],
                        "timezone": os.getenv("GENERIC_TIMEZONE", "UTC")
                    }
                    
                    if await post_to_orchestrator(payload):
                        processed_ids.add(msg_id)
                        new_count += 1
                except Exception as msg_err:
                    log_warning(logger, "gmail_message_processing_failed", msg_id=msg_id, error=str(msg_err))
            
            log_info(logger, "gmail_poll_tick", new_messages=new_count)
            
        except Exception as e:
            log_warning(logger, "gmail_poll_failed", error=str(e))
            
        await asyncio.sleep(config.poll_interval)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(gmail_poll_loop())

@app.get("/health")
def health():
    enabled = str(get_config_value("enable_gmail_sync", "true")).lower() == "true"
    return {"status": "ok", "gmail_enabled": enabled}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
