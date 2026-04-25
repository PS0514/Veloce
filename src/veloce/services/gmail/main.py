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

logger = get_logger(__name__)
app = FastAPI(title="Veloce Gmail Service", version="0.1.0")

class GmailConfig:
    def __init__(self):
        self.poll_interval = int(os.getenv("GMAIL_POLL_INTERVAL", "60"))
        self.webhook_url = os.getenv("N8N_WEBHOOK_URL") # Reusing N8N_WEBHOOK_URL as it points to Orchestrator
        self.enabled = os.getenv("ENABLE_GMAIL_SYNC", "true").lower() == "true"

config = GmailConfig()
gmail_client = GmailClient()

# State to keep track of processed email IDs to avoid duplicates
PROCESSED_MSGS_FILE = "data/processed_gmail_ids.txt"

def get_processed_ids() -> set:
    if os.path.exists(PROCESSED_MSGS_FILE):
        with open(PROCESSED_MSGS_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_processed_id(msg_id: str):
    os.makedirs("data", exist_ok=True)
    with open(PROCESSED_MSGS_FILE, "a") as f:
        f.write(f"{msg_id}\n")

async def post_to_orchestrator(payload: dict) -> bool:
    if not config.webhook_url:
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
    ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-context-ingest")
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
            async with session.post(ingest_url, json=ingest_payload, timeout=60) as resp:
                resp.raise_for_status()
        except Exception as e:
            log_warning(logger, "gmail_ingest_failed", error=str(e))
            # We don't set success = False here because the primary goal (scheduling) might have worked
            
    return success

async def gmail_poll_loop():
    log_info(logger, "gmail_poll_loop_start", interval=config.poll_interval)
    processed_ids = get_processed_ids()
    
    while True:
        if not config.enabled:
            await asyncio.sleep(60)
            continue
            
        try:
            # Calculate date for 3 days ago
            three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
            query = f"after:{three_days_ago.strftime('%Y/%m/%d')}"
            
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
                        save_processed_id(msg_id)
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
                        save_processed_id(msg_id)
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
    return {"status": "ok", "gmail_enabled": config.enabled}
