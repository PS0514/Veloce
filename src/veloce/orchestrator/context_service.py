from datetime import datetime, timezone

from veloce.orchestrator.logging_utils import get_logger, log_info
from veloce.orchestrator.db import SQLiteStore
from veloce.orchestrator.models import ContextItem, ContextRetrieveResponse

logger = get_logger(__name__)


class ContextService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    @staticmethod
    def _score_row(query: str, message: str, date_value: str | None) -> float:
        query_tokens = [token for token in query.lower().split() if token]
        msg = message.lower()

        if not query_tokens:
            text_match = 0.5
        else:
            hits = sum(1 for token in query_tokens if token in msg)
            text_match = hits / len(query_tokens)

        recency_weight = 0.0
        if date_value:
            try:
                ts = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 86400)
                recency_weight = 1 / (1 + age_days / 7)
            except ValueError:
                recency_weight = 0.0

        return round((0.7 * text_match) + (0.3 * recency_weight), 4)

    def retrieve_scheduled(self, limit: int = 10) -> list[dict]:
        rows = self.store.retrieve_scheduled_tasks(limit=limit)
        return [dict(row) for row in rows]

    def retrieve(
        self,
        *,
        chat_id: int,
        query: str,
        limit: int,
        since: str | None,
    ) -> ContextRetrieveResponse:
        log_info(
            logger,
            "context_retrieve_start",
            chat_id=chat_id,
            query=query,
            limit=limit,
            since=since,
        )
        rows = self.store.retrieve_context(chat_id=chat_id, query=query, limit=limit, since=since)

        scored: list[ContextItem] = []
        filtered_low_score = 0
        for row in rows:
            score = self._score_row(query, row["message"], row["date"])
            if query and score <= 0.15:
                filtered_low_score += 1
                continue
            
            bot_type = row["bot_type"] if "bot_type" in row.keys() else None
            is_automated = bool(bot_type)
            
            # Fallback: If not found in DB but text has bot signature, treat as automated
            if not is_automated and "[VeloceBot]" in (row["message"] or ""):
                is_automated = True
                bot_type = "userbot"

            prefix = f"[{bot_type.upper()}]" if is_automated else "[USER]"
            prefixed_message = f"{prefix}: {row['message']}"

            scored.append(
                ContextItem(
                    message_id=row["message_id"],
                    sender_id=row["sender_id"],
                    chat_title=row["chat_title"],
                    message=prefixed_message,
                    date=row["date"],
                    source=row["source"],
                    score=score,
                    is_automated=is_automated,
                    bot_type=bot_type,
                )
            )

        top = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
        log_info(
            logger,
            "context_retrieve_done",
            chat_id=chat_id,
            db_rows=len(rows),
            filtered_low_score=filtered_low_score,
            returned=len(top),
        )
        return ContextRetrieveResponse(
            chat_id=chat_id,
            query=query,
            returned=len(top),
            items=top,
        )

    def retrieve_trigger_context(self, chat_id: int, automated_msg_id: int) -> list[ContextItem]:
        """Retrieves the context surrounding the message that triggered an automated response."""
        trigger_id = self.store.retrieve_trigger_id(chat_id, automated_msg_id)
        if not trigger_id:
            return []
        
        # We find the trigger message and maybe a few before it
        # Since retrieve_context uses 'since', we might just find the exact message
        # But let's be smarter: get messages around that trigger_id
        
        log_info(logger, "context_retrieve_trigger", chat_id=chat_id, trigger_id=trigger_id)
        
        # Currently our DB doesn't support "get messages around X", 
        # but we can fetch context with an empty query and high limit, then filter.
        # Or better: Add a simple fetch for the exact message first.
        
        trigger_row = self.store.retrieve_message(chat_id, trigger_id)
        if not trigger_row:
            return []
            
        bot_type = trigger_row["bot_type"] if "bot_type" in trigger_row.keys() else None
        is_automated = bool(bot_type)
        
        # Fallback: If not found in DB but text has bot signature, treat as automated
        if not is_automated and "[VeloceBot]" in (trigger_row["message"] or ""):
            is_automated = True
            bot_type = "userbot"

        prefix = f"[{bot_type.upper()}]" if is_automated else "[USER]"
        prefixed_message = f"{prefix}: {trigger_row['message']}"

        return [
            ContextItem(
                message_id=trigger_row["message_id"],
                sender_id=trigger_row["sender_id"],
                chat_title=trigger_row["chat_title"],
                message=prefixed_message,
                date=trigger_row["date"],
                source=trigger_row["source"],
                score=1.0,
                is_automated=is_automated,
                bot_type=bot_type,
            )
        ]
