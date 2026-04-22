from datetime import datetime, timezone

from veloce.orchestrator.db import SQLiteStore
from veloce.orchestrator.models import ContextItem, ContextRetrieveResponse


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

    def retrieve(
        self,
        *,
        chat_id: int,
        query: str,
        limit: int,
        since: str | None,
    ) -> ContextRetrieveResponse:
        rows = self.store.retrieve_context(chat_id=chat_id, query=query, limit=limit, since=since)

        scored: list[ContextItem] = []
        for row in rows:
            score = self._score_row(query, row["message"], row["date"])
            if query and score <= 0.15:
                continue
            scored.append(
                ContextItem(
                    message_id=row["message_id"],
                    sender_id=row["sender_id"],
                    chat_title=row["chat_title"],
                    message=row["message"],
                    date=row["date"],
                    source=row["source"],
                    score=score,
                )
            )

        top = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
        return ContextRetrieveResponse(
            chat_id=chat_id,
            query=query,
            returned=len(top),
            items=top,
        )
