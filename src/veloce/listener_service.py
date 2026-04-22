import requests
from telethon import TelegramClient, events

from veloce.config import load_listener_config


def build_client() -> TelegramClient:
    config = load_listener_config()
    return TelegramClient("veloce_session", config.api_id, config.api_hash)


def run_listener() -> None:
    config = load_listener_config()
    client = build_client()

    def should_forward_text(raw_text: str) -> bool:
        text = raw_text.lower()
        return not config.keywords or any(keyword in text for keyword in config.keywords)

    def post_to_webhook(payload: dict) -> None:
        if not config.webhook_url:
            print("Webhook URL is not configured; skipping outbound payload.")
            return
        try:
            requests.post(config.webhook_url, json=payload, timeout=10)
        except requests.RequestException as exc:
            print(f"Webhook error: {exc}")

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
            return

        print(f"Sending startup history: last {limit} message(s) per allowed chat...")

        async for dialog in client.iter_dialogs():
            if not await is_allowed_dialog(dialog):
                continue

            async for message in client.iter_messages(dialog.entity, limit=limit):
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
                post_to_webhook(payload)

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not await is_allowed_chat(event):
            return

        if should_forward_text(event.raw_text):
            print(f"Intercepted relevant message: {event.raw_text[:50]}...")
            payload = {
                "source": "telegram_userbot",
                "message_id": event.id,
                "sender_id": event.sender_id,
                "chat_id": event.chat_id,
                "message": event.raw_text,
                "date": event.date.isoformat(),
            }
            post_to_webhook(payload)

    print("Veloce Listener is actively monitoring Telegram...")
    client.start()
    client.loop.run_until_complete(send_startup_history())
    client.run_until_disconnected()


if __name__ == "__main__":
    run_listener()
