import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

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

# State to keep track of last processed email ID
LAST_MSG_FILE = "data/last_gmail_id.txt"

def get_last_msg_id() -> Optional[str]:
    if os.path.exists(LAST_MSG_FILE):
        with open(LAST_MSG_FILE, "r") as f:
            return f.read().strip()
    return None

def save_last_msg_id(msg_id: str):
    os.makedirs("data", exist_ok=True)
    with open(LAST_MSG_FILE, "w") as f:
        f.write(msg_id)

async def post_to_orchestrator(payload: dict):
    if not config.webhook_url:
        return
    
    # Forward to scheduler
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(config.webhook_url, json=payload, timeout=60) as resp:
                resp.raise_for_status()
                log_info(logger, "gmail_forwarded_to_orchestrator", msg_id=payload.get("message_id"))
        except Exception as e:
            log_warning(logger, "gmail_forward_failed", error=str(e))

    # Forward to ingest
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

async def gmail_poll_loop():
    log_info(logger, "gmail_poll_loop_start", interval=config.poll_interval)
    last_id = get_last_msg_id()
    
    while True:
        if not config.enabled:
            await asyncio.sleep(60)
            continue
            
        try:
            # Poll for unread or just latest messages
            # For simplicity, let's poll for messages in INBOX
            messages_summary = gmail_client.list_messages(query="label:INBOX", max_results=10)
            
            new_messages = []
            for msg_summ in messages_summary:
                if msg_summ["id"] == last_id:
                    break
                new_messages.append(msg_summ["id"])
            
            if new_messages:
                # Process oldest first
                for msg_id in reversed(new_messages):
                    full_msg = gmail_client.get_message(msg_id)
                    parsed = gmail_client.parse_message(full_msg)
                    
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
                    
                    await post_to_orchestrator(payload)
                    last_id = msg_id
                    save_last_msg_id(last_id)
                    
            log_info(logger, "gmail_poll_tick", new_messages=len(new_messages))
            
        except Exception as e:
            log_warning(logger, "gmail_poll_failed", error=str(e))
            
        await asyncio.sleep(config.poll_interval)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(gmail_poll_loop())

@app.get("/health")
def health():
    return {"status": "ok", "gmail_enabled": config.enabled}

@app.post("/sync-last-week")
async def sync_last_week(background_tasks: BackgroundTasks):
    background_tasks.add_task(perform_sync_last_week)
    return {"status": "sync_started"}

async def perform_sync_last_week():
    log_info(logger, "gmail_sync_last_week_start")
    try:
        emails = gmail_client.fetch_emails_last_week()
        for email in emails:
            payload = {
                "source": "gmail_history",
                "message_id": email["id"],
                "sender_id": email["sender"],
                "chat_id": "gmail_inbox",
                "chat_title": f"Gmail: {email['subject']}",
                "message": f"Subject: {email['subject']}\nFrom: {email['sender']}\n\n{email['body']}",
                "date": email["date"],
                "timezone": os.getenv("GENERIC_TIMEZONE", "UTC")
            }
            # Only ingest to context, don't trigger scheduler for old emails unless explicitly wanted
            # Actually, the user might want them to be added to calendar? 
            # "extract useful info and add to calander"
            # If I send to orchestrator, it will try to schedule.
            # Let's send to orchestrator too.
            await post_to_orchestrator(payload)
            
        log_info(logger, "gmail_sync_last_week_done", count=len(emails))
    except Exception as e:
        log_warning(logger, "gmail_sync_last_week_failed", error=str(e))
