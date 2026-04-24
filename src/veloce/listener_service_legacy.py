import aiohttp
import asyncio
import requests
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from telethon import events
from telethon.sync import TelegramClient

from veloce.config import load_listener_config
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning

logger = get_logger(__name__)


def build_client() -> TelegramClient:
    config = load_listener_config()
    return TelegramClient(config.session_path, config.api_id, config.api_hash)


def run_listener() -> None:
    config = load_listener_config()

    log_info(
        logger,
        "listener_starting",
        session_path=config.session_path,
        webhook_url=config.webhook_url,
        startup_history_limit=config.startup_history_limit,
        keyword_filters=len(config.keywords),
        channel_filter_chat_ids=len(config.channel_chat_ids),
        channel_filter_usernames=len(config.channel_usernames),
    )

    if not config.api_id or not config.api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured before starting the listener."
        )
    
    # Check if session is already authenticated before trying to start
    session_file = Path(f"{config.session_path}.session")
    if not session_file.exists():
        raise RuntimeError(
            f"Telegram session not found at {session_file}. "
            "Please authenticate first by running the setup wizard (python scripts/run_setup.py) "
            "and completing Telegram phone/2FA authentication."
        )
    
    client = build_client()

    def should_forward_text(raw_text: str) -> bool:
        text = raw_text.lower()
        return not config.keywords or any(keyword in text for keyword in config.keywords)

    def message_preview(raw_text: str, limit: int = 80) -> str:
        text = raw_text.replace("\n", " ").strip()
        return text if len(text) <= limit else f"{text[:limit]}..."

    async def send_bot_notification(token: str, chat_id: str, text: str) -> dict:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        log_warning(logger, "bot_notification_failed", status=response.status, response=await response.text())
                    return await response.json()
        except Exception as exc:
            log_warning(logger, "bot_notification_error", error=str(exc))
            return {}

    async def post_to_webhook_async(payload: dict) -> list[dict]:
        if not config.webhook_url:
            log_warning(logger, "listener_webhook_skipped", reason="missing_webhook_url")
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(config.webhook_url, json=payload, timeout=120) as response:
                    response.raise_for_status()
                    data = await response.json()
                    # Orchestrator might return a list or a single dict
                    return data if isinstance(data, list) else [data]
        except Exception as exc:
            log_warning(logger, "listener_webhook_failed", error=str(exc))
            return []

    async def post_to_context_ingest_async(payload: dict) -> None:
        if not config.webhook_url:
            return
        ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-context-ingest")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(ingest_url, json=payload, timeout=10) as response:
                    response.raise_for_status()
        except Exception as exc:
            log_warning(
                logger,
                "listener_context_ingest_failed",
                source=payload.get("source"),
                chat_id=payload.get("chat_id"),
                message_id=payload.get("message_id"),
                error=str(exc),
            )

    async def post_batch_to_webhook_async(payloads: list[dict]) -> None:
        if not config.webhook_url or not payloads:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(config.webhook_url, json=payloads, timeout=60) as response:
                    log_info(
                        logger,
                        "listener_batch_webhook_success",
                        batch_size=len(payloads),
                        status=response.status,
                    )
                    response.raise_for_status()
        except Exception as exc:
            log_warning(logger, "listener_batch_webhook_failed", error=str(exc))

    async def post_batch_to_context_ingest_async(payloads: list[dict]) -> None:
        if not config.webhook_url or not payloads:
            return
        ingest_url = config.webhook_url.replace("/veloce-task-scheduler", "/telegram-context-ingest")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(ingest_url, json=payloads, timeout=60) as response:
                    log_info(logger, "listener_batch_context_ingest_success", batch_size=len(payloads))
                    response.raise_for_status()
        except Exception as exc:
            log_warning(logger, "listener_batch_context_ingest_failed", error=str(exc))

    async def is_allowed_chat(event) -> bool:
        # Always allow the notification chat so we can capture replies to the bot
        if config.notification_chat_id and str(event.chat_id) == str(config.notification_chat_id):
            return True

        if not config.channel_chat_ids and not config.channel_usernames:
            return True

        if event.chat_id in config.channel_chat_ids:
            return True

        if config.channel_usernames:
            chat = await event.get_chat()
            username = getattr(chat, "username", None)
            if username and username.lower() in config.channel_usernames:
                return True

        return False

    async def is_allowed_dialog(dialog) -> bool:
        # Always allow the notification chat
        if config.notification_chat_id and str(dialog.id) == str(config.notification_chat_id):
            return True

        if not config.channel_chat_ids and not config.channel_usernames:
            return True

        dialog_id = getattr(dialog, "id", None)
        if dialog_id in config.channel_chat_ids:
            return True

        entity = getattr(dialog, "entity", None)
        username = getattr(entity, "username", None)
        if username and username.lower() in config.channel_usernames:
            return True

        return False

    async def send_startup_history() -> None:
        limit = config.startup_history_limit
        if limit <= 0:
            log_info(logger, "listener_startup_history_skipped", reason="limit_zero")
            return

        log_info(logger, "listener_startup_history_begin", limit=limit)

        scanned_count = 0
        context_batch = []
        webhook_batch = []

        async for dialog in client.iter_dialogs():
            if not await is_allowed_dialog(dialog):
                continue

            async for message in client.iter_messages(dialog.entity, limit=limit):
                scanned_count += 1
                if not message.message:
                    continue

                payload = {
                    "source": "telegram_userbot_startup_history",
                    "message_id": message.id,
                    "sender_id": message.sender_id,
                    "chat_id": dialog.id,
                    "chat_title": dialog.name,
                    "message": message.message,
                    "date": message.date.isoformat() if message.date else None,
                }
                
                # 1. Add to context batch (all messages)
                context_batch.append(payload)
                
                # 2. Add to webhook batch only if it passes the keyword filter
                if should_forward_text(message.message):
                    webhook_batch.append(payload)

        # 3. Send the compiled batches over the network
        log_info(logger, "listener_startup_history_sending_batches", 
                 context_size=len(context_batch), 
                 webhook_size=len(webhook_batch))
                 
        await post_batch_to_context_ingest_async(context_batch)
        await post_batch_to_webhook_async(webhook_batch)

        log_info(
            logger,
            "listener_startup_history_done",
            scanned_messages=scanned_count,
            forwarded_messages=len(webhook_batch),
        )

    @client.on(events.NewMessage())
    async def handler(event):
        if not await is_allowed_chat(event):
            return

        chat = await event.get_chat()
        chat_title = getattr(chat, "title", getattr(chat, "username", None))
        
        payload = {
            "source": "telegram_userbot",
            "message_id": event.id,
            "sender_id": event.sender_id,
            "chat_id": event.chat_id,
            "chat_title": chat_title,
            "message": event.raw_text,
            "date": event.date.isoformat() if event.date else None,
        }
        
        # 1. Ingest context synchronously (using async version for safety in async loop)
        await post_to_context_ingest_async(payload)

        if should_forward_text(event.raw_text):
            log_info(
                logger,
                "listener_message_forward",
                source="telegram_userbot",
                chat_id=event.chat_id,
                message_id=event.id,
                sender_id=event.sender_id,
                preview=message_preview(event.raw_text),
            )
            # 2. Send to orchestrator and await the results
            results = await post_to_webhook_async(payload)
            
            for result in results:
                # 3. Check if clarification is needed
                if result.get("needs_clarification"):
                    question = result.get("clarification_question", "Can you provide more details?")
                    
                    if config.clarification_mode == "dm":
                        # -----------------------------------------------------
                        # MODE 1: SEND VIA PRIVATE DM (BOTFATHER)
                        # -----------------------------------------------------
                        if config.bot_token and config.notification_chat_id:
                            task_name = result.get("selected_task", {}).get("task_name", "a task")
                            safe_title = chat_title or "a chat"
                            msg_text = f"🚨 **Clarification needed from {safe_title}**\nTask: _{task_name}_\n\n❓ {question}"
                            
                            bot_resp = await send_bot_notification(config.bot_token, config.notification_chat_id, msg_text)
                            
                            # Ingest the BOT's question into context
                            bot_msg_id = bot_resp.get("result", {}).get("message_id", 0)
                            bot_context_payload = {
                                "source": "botfather_notification",
                                "message_id": bot_msg_id,
                                "sender_id": 0, # Representing the bot
                                "chat_id": int(config.notification_chat_id),
                                "chat_title": "Veloce Notifier",
                                "message": msg_text,
                                "date": datetime.now(timezone.utc).isoformat(),
                            }
                            await post_to_context_ingest_async(bot_context_payload)
                        else:
                            log_warning(logger, "clarification_dm_missing_config", reason="Clarification mode set to DM, but missing bot_token or notification_chat_id in config.")
                    else:
                        # -----------------------------------------------------
                        # MODE 2: DIRECT REPLY IN THE SAME GROUP CHAT
                        # -----------------------------------------------------
                        bot_reply = await event.reply(question)
                        
                        # Ingest the bot's reply into the group chat's context
                        bot_context_payload = {
                            "source": "telegram_userbot_reply",
                            "message_id": bot_reply.id,
                            "sender_id": bot_reply.sender_id,
                            "chat_id": event.chat_id,
                            "chat_title": chat_title,
                            "message": question,
                            "date": bot_reply.date.isoformat() if bot_reply.date else datetime.now(timezone.utc).isoformat(),
                        }
                        await post_to_context_ingest_async(bot_context_payload)

                # 5. Optional: Notify on success
                elif result.get("scheduled"):
                    task_name = result.get("selected_task", {}).get("task_name", "Task")
                    if config.clarification_mode == "dm" and config.bot_token and config.notification_chat_id:
                        await send_bot_notification(
                            config.bot_token, 
                            config.notification_chat_id, 
                            f"✅ Successfully scheduled: **{task_name}**"
                        )
                    else:
                        await event.reply(f"✅ Successfully scheduled: **{task_name}**")

    log_info(logger, "listener_connected_waiting_messages")
    try:
        client.connect()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Failed to open Telegram session database at {session_file}: {exc}. "
            "If this persists, stop all listener/setup processes and delete the session file so you can log in again."
        ) from exc

    try:
        if not client.is_user_authorized():
            raise RuntimeError(
                "Telegram session exists but is not authorized. Complete login in setup wizard first."
            )

        client.loop.run_until_complete(send_startup_history())
        client.run_until_disconnected()
    finally:
        client.disconnect()
        log_info(logger, "listener_disconnected")


if __name__ == "__main__":
    run_listener()
