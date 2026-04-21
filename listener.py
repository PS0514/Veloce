import os
import requests
from telethon import TelegramClient, events

API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL')
CHANNEL_FILTERS_RAW = os.getenv('TELEGRAM_CHANNEL_FILTERS', '')
KEYWORDS_RAW = os.getenv('LISTENER_KEYWORDS', 'assignment,deadline,due,exam,project')


def parse_channel_filters(raw_filters):
    chat_ids = set()
    usernames = set()
    for item in raw_filters.split(','):
        token = item.strip()
        if not token:
            continue
        if token.lstrip('-').isdigit():
            chat_ids.add(int(token))
        else:
            usernames.add(token.lstrip('@').lower())
    return chat_ids, usernames


def parse_keywords(raw_keywords):
    tokens = [item.strip().lower() for item in raw_keywords.split(',')]
    return [token for token in tokens if token]


CHANNEL_CHAT_IDS, CHANNEL_USERNAMES = parse_channel_filters(CHANNEL_FILTERS_RAW)
KEYWORDS = parse_keywords(KEYWORDS_RAW)

# Load the session created by setup.py
client = TelegramClient('veloce_session', API_ID, API_HASH)


async def is_allowed_chat(event):
    if not CHANNEL_CHAT_IDS and not CHANNEL_USERNAMES:
        return True

    if event.chat_id in CHANNEL_CHAT_IDS:
        return True

    if CHANNEL_USERNAMES:
        chat = await event.get_chat()
        username = getattr(chat, 'username', None)
        if username and username.lower() in CHANNEL_USERNAMES:
            return True

    return False

# Listen to incoming messages in specific groups or from specific professors
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if not await is_allowed_chat(event):
        return

    text = event.raw_text.lower()

    # Basic filter so we don't send every single message to n8n (saves API costs)
    if any(keyword in text for keyword in KEYWORDS):
        print(f"Intercepted relevant message: {text[:50]}...")

        # Forward the raw message to your n8n Webhook
        payload = {
            "source": "telegram_userbot",
            "sender_id": event.sender_id,
            "message": event.raw_text,
            "date": event.date.isoformat()
        }
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=10)
        except requests.RequestException as exc:
            print(f"Webhook error: {exc}")

print("🎧 Veloce Listener is actively monitoring Telegram...")
client.start()
client.run_until_disconnected()