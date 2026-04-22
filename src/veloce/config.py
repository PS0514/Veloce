import os
from dataclasses import dataclass
from typing import Set


@dataclass(frozen=True)
class ListenerConfig:
    api_id: str | None
    api_hash: str | None
    webhook_url: str | None
    channel_chat_ids: Set[int]
    channel_usernames: Set[str]
    keywords: list[str]


def parse_channel_filters(raw_filters: str) -> tuple[Set[int], Set[str]]:
    chat_ids: Set[int] = set()
    usernames: Set[str] = set()

    for item in raw_filters.split(","):
        token = item.strip()
        if not token:
            continue
        if token.lstrip("-").isdigit():
            chat_ids.add(int(token))
        else:
            usernames.add(token.lstrip("@").lower())

    return chat_ids, usernames


def parse_keywords(raw_keywords: str) -> list[str]:
    return [part.strip().lower() for part in raw_keywords.split(",") if part.strip()]


def load_listener_config() -> ListenerConfig:
    channels_raw = os.getenv("TELEGRAM_CHANNEL_FILTERS", "")
    keywords_raw = os.getenv("LISTENER_KEYWORDS", "")
    channel_chat_ids, channel_usernames = parse_channel_filters(channels_raw)

    return ListenerConfig(
        api_id=os.getenv("TELEGRAM_API_ID"),
        api_hash=os.getenv("TELEGRAM_API_HASH"),
        webhook_url=os.getenv("N8N_WEBHOOK_URL"),
        channel_chat_ids=channel_chat_ids,
        channel_usernames=channel_usernames,
        keywords=parse_keywords(keywords_raw),
    )
