import requests
from telethon import TelegramClient, events

from veloce.config import load_listener_config


def build_client() -> TelegramClient:
    config = load_listener_config()
    return TelegramClient("veloce_session", config.api_id, config.api_hash)


def run_listener() -> None:
    config = load_listener_config()
    client = build_client()

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

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not await is_allowed_chat(event):
            return

        text = event.raw_text.lower()
        keyword_matched = not config.keywords or any(keyword in text for keyword in config.keywords)
        if keyword_matched:
            print(f"Intercepted relevant message: {text[:50]}...")
            payload = {
                "source": "telegram_userbot",
                "sender_id": event.sender_id,
                "message": event.raw_text,
                "date": event.date.isoformat(),
            }
            try:
                requests.post(config.webhook_url, json=payload, timeout=10)
            except requests.RequestException as exc:
                print(f"Webhook error: {exc}")

    print("Veloce Listener is actively monitoring Telegram...")
    client.start()
    client.run_until_disconnected()


if __name__ == "__main__":
    run_listener()
