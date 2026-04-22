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
    startup_history_limit: int
    orchestrator_url: str | None
    db_path: str | None
    session_path: str


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


def parse_positive_int(raw_value: str | None, default: int) -> int:
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return max(0, parsed)


def load_listener_config() -> ListenerConfig:
    channels_raw = os.getenv("TELEGRAM_CHANNEL_FILTERS", "")
    keywords_raw = os.getenv("LISTENER_KEYWORDS", "")
    channel_chat_ids, channel_usernames = parse_channel_filters(channels_raw)
    startup_history_limit = parse_positive_int(os.getenv("LISTENER_STARTUP_HISTORY_LIMIT", "10"), 10)

    orchestrator_url = os.getenv("VELOCE_ORCHESTRATOR_URL")
    webhook_url = orchestrator_url or os.getenv("N8N_WEBHOOK_URL")
    
    # Use data directory for session storage
    db_path = os.getenv("VELOCE_DB_PATH", "data/veloce.db")
    data_dir = os.path.dirname(db_path) or "data"
    os.makedirs(data_dir, exist_ok=True)
    session_path = os.path.join(data_dir, "telegram_session")

    return ListenerConfig(
        api_id=os.getenv("TELEGRAM_API_ID"),
        api_hash=os.getenv("TELEGRAM_API_HASH"),
        webhook_url=webhook_url,
        channel_chat_ids=channel_chat_ids,
        channel_usernames=channel_usernames,
        keywords=parse_keywords(keywords_raw),
        startup_history_limit=startup_history_limit,
        orchestrator_url=orchestrator_url,
        db_path=db_path,
        session_path=session_path,
    )
