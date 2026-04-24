import os
from dataclasses import dataclass
from typing import Set

from veloce.runtime_config import get_config_value


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
    bot_token: str | None
    notification_chat_id: str | None
    clarification_mode: str


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
    # Runtime config (veloce_config.json) takes priority, .env is fallback
    channels_raw = get_config_value("telegram_channel_filters") or os.getenv("TELEGRAM_CHANNEL_FILTERS", "")
    keywords_raw = get_config_value("listener_keywords") or os.getenv("LISTENER_KEYWORDS", "")
    channel_chat_ids, channel_usernames = parse_channel_filters(channels_raw)
    startup_history_limit = parse_positive_int(os.getenv("LISTENER_STARTUP_HISTORY_LIMIT", "10"), 10)

    orchestrator_url = os.getenv("VELOCE_ORCHESTRATOR_URL")
    webhook_url = orchestrator_url or os.getenv("N8N_WEBHOOK_URL")
    
    # Use data directory for session storage
    db_path = os.getenv("VELOCE_DB_PATH", "data/veloce.db")
    data_dir = os.path.dirname(db_path) or "data"
    os.makedirs(data_dir, exist_ok=True)
    session_path = os.path.join(data_dir, "telegram_session")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    notification_chat_id = get_config_value("notification_chat_id") or os.getenv("TELEGRAM_NOTIFICATION_CHAT_ID")
    clarification_mode = get_config_value("clarification_mode", "group")

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
        bot_token=bot_token,
        notification_chat_id=notification_chat_id,
        clarification_mode=clarification_mode,
    )
