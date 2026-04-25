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
me: Optional[object] = None # To store bot's own user object

# Batching storage: chat_id -> List of messages
pending_batches = {}
batch_locks = {}
BATCH_WINDOW_SECONDS = 2.0

async def process_batch(chat_id: int):
    """Wait for the window, then process all collected messages for a chat."""
    await asyncio.sleep(BATCH_WINDOW_SECONDS)
    
    async with batch_locks[chat_id]:
        batch = pending_batches.pop(chat_id, [])
        if not batch:
            return

    log_info(logger, "telegram_service_processing_batch", chat_id=chat_id, size=len(batch))
    
    # Context ingest (all messages)
    await post_to_context_ingest_async(batch)
    
    # Webhook forward (all messages that pass keywords, Orchestrator will filter bots)
    webhook_payload = [
        m for m in batch 
        if (not config.keywords or any(k in m["message"].lower() for k in config.keywords))
    ]
    
    if not webhook_payload:
        return

    # Call orchestrator
    results = await post_batch_to_webhook_async(webhook_payload)
    
    # Handle results
    for result in results:
        automated_payloads = []
        state = result.get("state")

        if state == "ignored_group_chat":
            log_info(logger, "telegram_service_ignoring_general_chat", chat_id=chat_id)
            continue

        if result.get("needs_clarification"):
            question = result.get("clarification_question", "Details?")
            task_obj = result.get('selected_task') or {}
            task_name = task_obj.get('task_name', 'Unknown') if isinstance(task_obj, dict) else getattr(task_obj, 'task_name', 'Unknown')
            res_chat_title = result.get("chat_title", "Unknown Chat")
            src_chat_id = result.get("source_chat_id", "")
            src_msg_id = result.get("source_message_id", "")

            # Respect clarification_mode
            use_dm = config.clarification_mode == "dm"
            sent_dm = False
            
            if use_dm and config.notification_chat_id:
                notif_text = (
                    f"❓ **[VeloceBot] Clarification Needed**\n"
                    f"Task: {task_name}\n"
                    f"Question: {question}\n"
                    f"Source: {res_chat_title}\n"
                    f"`[Ref:{src_chat_id}:{src_msg_id}]`"
                )
                res = await send_notification_internal(notif_text)
                if res.get("status") == "sent":
                    automated_payloads.append({
                        "chat_id": res["chat_id"],
                        "message_id": res["message_id"],
                        "bot_type": res["bot_type"],
                        "trigger_msg_id": src_msg_id,
                        "task_name": task_name
                    })
                sent_dm = True
                log_info(logger, "clarification_sent_dm", chat_id=config.notification_chat_id)
            
            if not use_dm or not sent_dm:
                try:
                    if client:
                        reply_text = (
                            f"❓ **[VeloceBot] Clarification Needed**\n"
                            f"Task: {task_name}\n"
                            f"Question: {question}\n"
                            f"`[Ref:{src_chat_id}:{src_msg_id}]`"
                        )
                        msg = await client.send_message(chat_id, reply_text)
                        automated_payloads.append({
                            "chat_id": chat_id,
                            "message_id": msg.id,
                            "bot_type": "userbot",
                            "trigger_msg_id": src_msg_id,
                            "task_name": task_name
                        })
                        log_info(logger, "clarification_sent_group", chat_id=chat_id)
                except Exception as exc:
                    log_warning(logger, "clarification_send_failed", error=str(exc))
                    if not sent_dm and config.notification_chat_id:
                        await send_notification_internal(f"❓ **Clarification Needed** (Send Failed)\nQuestion: {question}")

            elif result.get("scheduled"):
                task_name = result.get("selected_task", {}).get("task_name", "Task")
                res_chat_title = result.get("chat_title", "Unknown Chat")
                src_msg_id = result.get("source_message_id")
                
                if client:
                    msg = await client.send_message(chat_id, f"✅ **[VeloceBot] Scheduled**: **{task_name}**")
                    automated_payloads.append({
                        "chat_id": chat_id,
                        "message_id": msg.id,
                        "bot_type": "userbot",
                        "trigger_msg_id": src_msg_id,
                        "task_name": task_name
                    })
                
                if config.notification_chat_id:
                    notif_text = f"🚀 Task Scheduled\nTask: {task_name}\nSource: {res_chat_title}"
                    res = await send_notification_internal(notif_text)
                    if res.get("status") == "sent":
                        automated_payloads.append({
                            "chat_id": res["chat_id"],
                            "message_id": res["message_id"],
                            "bot_type": res["bot_type"],
                            "trigger_msg_id": src_msg_id,
                            "task_name": task_name
                        })

            elif state in ("general_chat_replied", "calendar_query_answered", "memory_saved"):
                reason = result.get("reason", "")
                src_msg_id = result.get("source_message_id")
                if client:
                    try:
                        msg = await client.send_message(chat_id, reason, reply_to=src_msg_id)
                        automated_payloads.append({
                            "chat_id": chat_id,
                            "message_id": msg.id,
                            "bot_type": "userbot",
                            "trigger_msg_id": src_msg_id,
                            "task_name": "General Chat"
                        })
                        log_info(logger, "telegram_service_sent_reply", chat_id=chat_id, state=state)
                    except Exception as exc:
                        log_warning(logger, "telegram_service_reply_failed", error=str(exc))
            
            if automated_payloads:
                await post_to_automated_ingest_async(automated_payloads)

async def post_to_automated_ingest_async(payload: dict | list[dict], max_retries: int = 3) -> bool:
    if not config.webhook_url:
        return False
    ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-automated-message-ingest")
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(ingest_url, json=payload, timeout=20) as response:
                    response.raise_for_status()
                    return True
        except Exception as exc:
            log_warning(logger, "telegram_service_automated_ingest_failed", attempt=attempt+1, error=str(exc))
            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
    return False

async def send_bot_notification(token: str, chat_id: str, text: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        log_info(logger, "bot_notification_sending", chat_id=chat_id, text_preview=text[:100])
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=20) as response:
                resp_data = await response.json()
                if response.status != 200:
                    log_warning(logger, "bot_notification_failed", status=response.status, response=await response.text())
                    return {"status": "error", "error": "API Failure"}
                
                log_info(logger, "bot_notification_sent", chat_id=chat_id, message_id=resp_data.get("result", {}).get("message_id"))
                return {
                    "status": "sent",
                    "bot_type": "fatherbot",
                    "chat_id": chat_id,
                    "message_id": resp_data.get("result", {}).get("message_id")
                }
    except Exception as exc:
        log_warning(logger, "bot_notification_error", error=str(exc))
        return {"status": "error", "error": str(exc)}

async def send_notification_internal(text: str, use_bot: bool = True) -> dict:
    # Add prefix for clear identification
    if not text.strip().startswith("[VeloceBot]"):
        text = f"**[VeloceBot]**\n{text}"
    
    log_info(logger, "notification_internal_start", use_bot=use_bot, chat_id=config.notification_chat_id, text_preview=text[:100])
    
    # 1. Try Bot first if requested
    if use_bot and config.bot_token and config.notification_chat_id:
        result = await send_bot_notification(config.bot_token, config.notification_chat_id, text)
        if result.get("status") == "sent":
            return result
        log_warning(logger, "bot_notification_failed_falling_back", error=result.get("error"))
    
    # 2. Fallback to Userbot (Client)
    if client and config.notification_chat_id:
        try:
            # Handle potential string chat_ids from config
            target_chat_id = config.notification_chat_id
            if isinstance(target_chat_id, str):
                if target_chat_id.startswith("-100") or target_chat_id.startswith("-"):
                    target_chat_id = int(target_chat_id)
                elif target_chat_id.isdigit():
                    target_chat_id = int(target_chat_id)
            
            msg = await client.send_message(target_chat_id, text)
            log_info(logger, "userbot_notification_sent", chat_id=config.notification_chat_id, message_id=msg.id)
            return {
                "status": "sent",
                "bot_type": "userbot",
                "chat_id": target_chat_id,
                "message_id": msg.id
            }
        except Exception as exc:
            log_warning(logger, "userbot_notification_failed", error=str(exc))
            return {"status": "error", "error": str(exc)}
            
    log_warning(logger, "notification_skipped", client_ready=client is not None, chat_id=config.notification_chat_id)
    return {"status": "skipped"}

# Helper to extract bot ID
def get_bot_id():
    if config.bot_token:
        try:
            return int(config.bot_token.split(":")[0])
        except Exception:
            pass
    return None

async def post_to_webhook_async(payload: dict) -> list[dict]:
    if not config.webhook_url:
        return []
    try:
        log_info(logger, "telegram_service_sending_webhook", 
                 chat_id=payload.get("chat_id"), 
                 message_id=payload.get("message_id"),
                 source=payload.get("source"))
        async with aiohttp.ClientSession() as session:
            async with session.post(config.webhook_url, json=payload, timeout=120) as response:
                response.raise_for_status()
                data = await response.json()
                return data if isinstance(data, list) else [data]
    except Exception as exc:
        log_warning(logger, "telegram_service_webhook_failed", error=str(exc))
        return []

# FIX 1: Prevent retry loops that cause duplicate GLM requests
async def post_batch_to_webhook_async(payloads: list[dict], max_retries: int = 1) -> list[dict]:
    if not config.webhook_url or not payloads:
        return []
    
    for attempt in range(max_retries):
        try:
            ids = [f"{p.get('chat_id')}:{p.get('message_id')}" for p in payloads]
            log_info(logger, "telegram_service_sending_batch_webhook", count=len(payloads), ids=ids)

            async with aiohttp.ClientSession() as session:
                async with session.post(config.webhook_url, json=payloads, timeout=300) as response:
                    response.raise_for_status()
                    data = await response.json()
                    log_info(logger, "telegram_service_batch_webhook_success", batch_size=len(payloads))
                    return data if isinstance(data, list) else [data]
        except Exception as exc:
            log_warning(logger, "telegram_service_batch_webhook_failed", attempt=attempt+1, error=str(exc))
            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
    return []

async def post_to_context_ingest_async(payload: dict | list[dict], max_retries: int = 3) -> bool:
    if not config.webhook_url:
        return False
    ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-context-ingest")
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(ingest_url, json=payload, timeout=60) as response:
                    response.raise_for_status()
                    return True
        except Exception as exc:
            log_warning(logger, "telegram_service_context_ingest_failed", attempt=attempt+1, error=str(exc))
            if attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
    return False

async def wait_for_orchestrator():
    """Wait for the orchestrator to be healthy before proceeding."""
    if not config.webhook_url:
        return
    
    from urllib.parse import urlparse
    parsed = urlparse(config.webhook_url)
    health_url = f"{parsed.scheme}://{parsed.netloc}/health"
    
    log_info(logger, "telegram_waiting_for_orchestrator", url=health_url)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(health_url, timeout=5) as resp:
                    if resp.status == 200:
                        log_info(logger, "telegram_orchestrator_ready")
                        return
            except Exception:
                pass
            await asyncio.sleep(3)

async def is_allowed_chat(chat_id: int, username: Optional[str] = None) -> bool:
    # ... (rest of function remains same)
    # 1. Always allow the notification chat (for responding to clarifications)
    if config.notification_chat_id:
        try:
            if str(chat_id) == str(config.notification_chat_id):
                return True
        except (ValueError, TypeError):
            pass
    
    # 2. If using a bot for notifications, also allow the DM with that bot
    if config.bot_token:
        try:
            bot_id = int(config.bot_token.split(":")[0])
            if chat_id == bot_id:
                return True
        except (ValueError, IndexError):
            pass

    # 3. If no filters are defined, allow everything
    if not config.channel_chat_ids and not config.channel_usernames:
        return True
    
    # 4. Check chat ID
    if chat_id in config.channel_chat_ids:
        return True
    
    # 5. Check username
    if username and username.lower() in config.channel_usernames:
        return True
    
    return False

def get_readable_chat_title(chat, username: Optional[str] = None) -> str:
    # 1. Try Chat/Channel title (Groups/Channels)
    title = getattr(chat, "title", None)
    if title:
        return str(title)
    
    # 2. Try User first/last name (DMs)
    first_name = getattr(chat, "first_name", None)
    if first_name:
        last_name = getattr(chat, "last_name", None)
        if last_name:
            return f"{first_name} {last_name}"
        return str(first_name)
    
    # 3. Fallback to username
    if username:
        return f"@{username}"
    
    # 4. Ultimate fallback
    return "Private Chat"

# FIX 2: Process Startup History Per-Channel to prevent payload crashes
async def send_startup_history(client: TelegramClient):
    await wait_for_orchestrator()
    limit_date = None
    if config.startup_history_days > 0:
        limit_date = datetime.now(timezone.utc) - timedelta(days=config.startup_history_days)
        log_info(logger, "telegram_startup_history_time_limit", days=config.startup_history_days, limit_date=limit_date.isoformat())

    scanned_count = 0
    bot_id = get_bot_id()

    async for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        username = getattr(entity, "username", None)
        
        if not await is_allowed_chat(dialog.id, username):
            continue

        chat_title = get_readable_chat_title(entity, username)
        context_batch = []
        webhook_batch = []

        async for message in client.iter_messages(dialog.entity, limit=config.startup_history_limit):
            if limit_date and message.date and message.date < limit_date:
                break

            scanned_count += 1
            if not message.message:
                continue

            # Identify bot messages
            is_bot = (message.sender_id == me.id) or (bot_id and message.sender_id == bot_id)

            payload = {
                "source": "telegram_startup_history",
                "message_id": message.id,
                "sender_id": message.sender_id,
                "chat_id": dialog.id,
                "chat_title": chat_title,
                "message": message.message,
                "date": message.date.isoformat() if message.date else None,
                "is_bot": is_bot
            }
            # Always add to context
            context_batch.append(payload)
            
            # Forward to scheduler if passes keywords (Orchestrator DB handles bot filtering)
            if not config.keywords or any(k in message.message.lower() for k in config.keywords):
                webhook_batch.append(payload)

        # Dispatch batches per dialog instead of globally
        if context_batch:
            await post_to_context_ingest_async(context_batch)
        
        if webhook_batch:
            log_info(logger, "telegram_startup_history_forwarding", count=len(webhook_batch), chat_id=dialog.id)
            results = await post_batch_to_webhook_async(webhook_batch)
            for result in results:
                automated_payloads = []
                if result.get("scheduled"):
                    task_name = result.get("selected_task", {}).get("task_name", "Task")
                    res_chat_title = result.get("chat_title") or chat_title
                    src_msg_id = result.get("source_message_id")
                    if config.notification_chat_id:
                        notif_text = f"🚀 Task Scheduled (Missed)\nTask: {task_name}\nSource: {res_chat_title}"
                        res = await send_notification_internal(notif_text)
                        if res.get("status") == "sent":
                            automated_payloads.append({
                                "chat_id": res["chat_id"],
                                "message_id": res["message_id"],
                                "bot_type": res["bot_type"],
                                "trigger_msg_id": src_msg_id,
                                "task_name": task_name
                            })
                elif result.get("needs_clarification"):
                    task_obj = result.get('selected_task') or {}
                    task_name = task_obj.get('task_name', 'Unknown') if isinstance(task_obj, dict) else getattr(task_obj, 'task_name', 'Unknown')
                    question = result.get("clarification_question", "Details?")
                    res_chat_title = result.get("chat_title") or chat_title
                    src_chat_id = result.get("source_chat_id", "")
                    src_msg_id = result.get("source_message_id", "")
                    
                    if config.notification_chat_id:
                        notif_text = (
                            f"❓ **Clarification Needed** (Missed)\n"
                            f"Task: {task_name}\n"
                            f"Question: {question}\n"
                            f"Source: {res_chat_title}\n"
                            f"`[Ref:{src_chat_id}:{src_msg_id}]`"
                        )
                        res = await send_notification_internal(notif_text)
                        if res.get("status") == "sent":
                            automated_payloads.append({
                                "chat_id": res["chat_id"],
                                "message_id": res["message_id"],
                                "bot_type": res["bot_type"],
                                "trigger_msg_id": src_msg_id,
                                "task_name": task_name
                            })
                
                if automated_payloads:
                    await post_to_automated_ingest_async(automated_payloads)
    
    log_info(logger, "telegram_startup_history_done", scanned=scanned_count)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, me
    log_info(logger, "telegram_service_starting")
    client = TelegramClient(config.session_path, config.api_id, config.api_hash)
    
    await client.start()
    me = await client.get_me()
    log_info(logger, "telegram_service_connected", me_id=me.id, me_username=me.username)
    
    bot_id = get_bot_id()

    @client.on(events.NewMessage())
    async def handler(event):
        # Identify if the message is from our bot/userbot
        is_bot = (event.sender_id == me.id) or (bot_id and event.sender_id == bot_id)

        chat = await event.get_chat()
        
        # If it's a group/channel, 'chat' should be the group.
        # If get_chat() returned the sender, we try to get the actual chat entity.
        actual_chat = chat
        if hasattr(chat, "first_name") and not event.is_private:
             # chat is a User, but it's not a private chat, so we need the group
             actual_chat = await event.get_input_chat()
             # Re-fetch full entity to get the title if needed
             actual_chat = await client.get_entity(actual_chat)

        username = getattr(actual_chat, "username", None)
        
        if not await is_allowed_chat(event.chat_id, username):
            return

        chat_id = event.chat_id
        chat_title = get_readable_chat_title(actual_chat, username)
        
        # Check if replying to the bot
        reply_to_me = False
        reply_to_msg_id = None
        reply_to_text = None
        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.sender_id == me.id:
                reply_to_me = True
                reply_to_msg_id = reply.id
                reply_to_text = reply.message

        payload = {
            "source": "telegram_userbot",
            "message_id": event.id,
            "sender_id": event.sender_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "message": event.raw_text,
            "date": event.date.isoformat() if event.date else None,
            "reply_to_me": reply_to_me,
            "reply_to_msg_id": reply_to_msg_id,
            "reply_to_text": reply_to_text,
            "is_bot": is_bot
        }
        
        # Batching logic
        if chat_id not in batch_locks:
            batch_locks[chat_id] = asyncio.Lock()
        
        async with batch_locks[chat_id]:
            if chat_id not in pending_batches:
                pending_batches[chat_id] = []
                # Start processing task for this new batch
                asyncio.create_task(process_batch(chat_id))
            
            pending_batches[chat_id].append(payload)
            log_info(logger, "telegram_message_batched", chat_id=chat_id, message_id=event.id)

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
    log_info(logger, "telegram_send_message_request", chat_id=payload.chat_id, text_preview=payload.text[:100])
    msg = await client.send_message(payload.chat_id, payload.text, reply_to=payload.reply_to)
    log_info(logger, "telegram_send_message_success", chat_id=payload.chat_id, message_id=msg.id)
    return {
        "status": "sent",
        "bot_type": "userbot",
        "chat_id": payload.chat_id,
        "message_id": msg.id
    }

@app.post("/send-notification")
async def send_notification(payload: NotificationRequest):
    result = await send_notification_internal(payload.text, use_bot=payload.use_bot)
    if result.get("status") == "skipped":
        raise HTTPException(status_code=400, detail="Notification chat not configured or client unavailable")
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)