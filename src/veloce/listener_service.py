import requests
import sqlite3
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

    def post_to_webhook(payload: dict) -> None:
        if not config.webhook_url:
            log_warning(logger, "listener_webhook_skipped", reason="missing_webhook_url")
            return
        try:
            response = requests.post(config.webhook_url, json=payload, timeout=10)
            body_preview = (response.text or "").replace("\n", " ").strip()
            if len(body_preview) > 200:
                body_preview = f"{body_preview[:200]}..."
            log_info(
                logger,
                "listener_webhook_response",
                source=payload.get("source"),
                chat_id=payload.get("chat_id"),
                message_id=payload.get("message_id"),
                status=response.status_code,
                body_preview=body_preview,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log_warning(
                logger,
                "listener_webhook_failed",
                source=payload.get("source"),
                chat_id=payload.get("chat_id"),
                message_id=payload.get("message_id"),
                error=exc,
            )

    async def is_allowed_chat(event) -> bool:
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

        posted_count = 0
        scanned_count = 0

        async for dialog in client.iter_dialogs():
            if not await is_allowed_dialog(dialog):
                continue

            async for message in client.iter_messages(dialog.entity, limit=limit):
                scanned_count += 1
                if not message.message:
                    continue
                if not should_forward_text(message.message):
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
                log_info(
                    logger,
                    "listener_startup_history_forward",
                    chat=dialog.name,
                    chat_id=dialog.id,
                    message_id=message.id,
                    preview=message_preview(message.message),
                )
                post_to_webhook(payload)
                posted_count += 1

        log_info(
            logger,
            "listener_startup_history_done",
            scanned_messages=scanned_count,
            forwarded_messages=posted_count,
        )

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not await is_allowed_chat(event):
            log_info(logger, "listener_message_skipped", reason="chat_not_allowed", chat_id=event.chat_id)
            return

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
            payload = {
                "source": "telegram_userbot",
                "message_id": event.id,
                "sender_id": event.sender_id,
                "chat_id": event.chat_id,
                "message": event.raw_text,
                "date": event.date.isoformat(),
            }
            post_to_webhook(payload)
        else:
            log_info(
                logger,
                "listener_message_skipped",
                reason="keyword_filter_miss",
                chat_id=event.chat_id,
                message_id=event.id,
            )

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
