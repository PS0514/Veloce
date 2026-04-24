import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from telethon import TelegramClient, events
import aiohttp

from veloce.config import load_listener_config
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning

logger = get_logger(__name__)

class MessageRequest(BaseModel):
    chat_id: int
    text: str
    reply_to: Optional[int] = None

class NotificationRequest(BaseModel):
    text: str
    use_bot: bool = True

# Configuration
config = load_listener_config()

# Global client
client: Optional[TelegramClient] = None

async def send_bot_notification(token: str, chat_id: str, text: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=20) as response:
                if response.status != 200:
                    log_warning(logger, "bot_notification_failed", status=response.status, response=await response.text())
                return await response.json()
    except Exception as exc:
        log_warning(logger, "bot_notification_error", error=str(exc))
        return {}

async def post_to_webhook_async(payload: dict) -> list[dict]:
    if not config.webhook_url:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(config.webhook_url, json=payload, timeout=120) as response:
                response.raise_for_status()
                data = await response.json()
                return data if isinstance(data, list) else [data]
    except Exception as exc:
        log_warning(logger, "telegram_service_webhook_failed", error=str(exc))
        return []

async def post_batch_to_webhook_async(payloads: list[dict]) -> None:
    if not config.webhook_url or not payloads:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(config.webhook_url, json=payloads, timeout=60) as response:
                response.raise_for_status()
                log_info(logger, "telegram_service_batch_webhook_success", batch_size=len(payloads))
    except Exception as exc:
        log_warning(logger, "telegram_service_batch_webhook_failed", error=str(exc))

async def post_to_context_ingest_async(payload: dict | list[dict]) -> None:
    if not config.webhook_url:
        return
    ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-context-ingest")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(ingest_url, json=payload, timeout=60) as response:
                response.raise_for_status()
    except Exception as exc:
        log_warning(logger, "telegram_service_context_ingest_failed", error=str(exc))

async def is_allowed_chat(chat_id: int, username: Optional[str] = None) -> bool:
    # 1. Always allow the notification chat
    if config.notification_chat_id and str(chat_id) == str(config.notification_chat_id):
        return True
    
    # 2. If no filters are defined, allow everything
    if not config.channel_chat_ids and not config.channel_usernames:
        return True
    
    # 3. Check chat ID
    if chat_id in config.channel_chat_ids:
        return True
    
    # 4. Check username
    if username and username.lower() in config.channel_usernames:
        return True
    
    return False

async def send_startup_history(client: TelegramClient):
    # Determine the time limit
    limit_date = None
    if config.startup_history_days > 0:
        limit_date = datetime.now(timezone.utc) - timedelta(days=config.startup_history_days)
        log_info(logger, "telegram_startup_history_time_limit", days=config.startup_history_days, limit_date=limit_date.isoformat())

    scanned_count = 0
    context_batch = []
    webhook_batch = []

    async for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        username = getattr(entity, "username", None)
        
        if not await is_allowed_chat(dialog.id, username):
            continue

        # Using both count limit and date limit if provided
        async for message in client.iter_messages(dialog.entity, limit=config.startup_history_limit):
            # STRICTOR: If we have a time limit, stop if the message is older than that limit
            if limit_date and message.date and message.date < limit_date:
                break

            scanned_count += 1
            if not message.message:
                continue

            payload = {
                "source": "telegram_startup_history",
                "message_id": message.id,
                "sender_id": message.sender_id,
                "chat_id": dialog.id,
                "chat_title": dialog.name,
                "message": message.message,
                "date": message.date.isoformat() if message.date else None,
            }
            context_batch.append(payload)
            if not config.keywords or any(k in message.message.lower() for k in config.keywords):
                webhook_batch.append(payload)

    if context_batch:
        await post_to_context_ingest_async(context_batch)
    
    if webhook_batch:
        log_info(logger, "telegram_startup_history_forwarding", count=len(webhook_batch))
        for payload in webhook_batch:
            # Process sequentially to avoid thundering herd on Orchestrator/AI/Calendar
            await post_to_webhook_async(payload)
            # Small sleep to give services breathing room
            await asyncio.sleep(0.1)
    
    log_info(logger, "telegram_startup_history_done", scanned=scanned_count, forwarded=len(webhook_batch))

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    log_info(logger, "telegram_service_starting")
    client = TelegramClient(config.session_path, config.api_id, config.api_hash)
    
    @client.on(events.NewMessage())
    async def handler(event):
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        
        if not await is_allowed_chat(event.chat_id, username):
            return

        chat_title = getattr(chat, "title", username)
        payload = {
            "source": "telegram_userbot",
            "message_id": event.id,
            "sender_id": event.sender_id,
            "chat_id": event.chat_id,
            "chat_title": chat_title,
            "message": event.raw_text,
            "date": event.date.isoformat() if event.date else None,
        }
        await post_to_context_ingest_async(payload)
        
        should_forward = not config.keywords or any(k in event.raw_text.lower() for k in config.keywords)
        if should_forward:
            results = await post_to_webhook_async(payload)
            for result in results:
                if result.get("needs_clarification"):
                    await event.reply(result.get("clarification_question", "Details?"))
                elif result.get("scheduled"):
                    task_name = result.get("selected_task", {}).get("task_name", "Task")
                    await event.reply(f"✅ Scheduled: **{task_name}**")

    await client.start()
    log_info(logger, "telegram_service_connected")
    asyncio.create_task(send_startup_history(client))
    yield
    await client.disconnect()

app = FastAPI(title="Veloce Telegram Service", version="0.1.0", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok", "connected": client is not None and client.is_connected()}

@app.post("/send-message")
async def send_message(payload: MessageRequest):
    if not client: raise HTTPException(status_code=503)
    await client.send_message(payload.chat_id, payload.text, reply_to=payload.reply_to)
    return {"status": "sent"}

@app.post("/send-notification")
async def send_notification(payload: NotificationRequest):
    if payload.use_bot and config.bot_token and config.notification_chat_id:
        resp = await send_bot_notification(config.bot_token, config.notification_chat_id, payload.text)
        return {"status": "bot_notified", "resp": resp}
    
    if not client: raise HTTPException(status_code=503)
    if not config.notification_chat_id: raise HTTPException(status_code=400)
    await client.send_message(int(config.notification_chat_id), payload.text)
    return {"status": "userbot_notified"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
