import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContextRow:
    chat_id: int
    message_id: int
    sender_id: int | None
    chat_title: str | None
    message: str
    source: str
    date: str | None


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    sender_id INTEGER,
                    chat_title TEXT,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    date TEXT,
                    inserted_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(chat_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_context_chat_date
                ON telegram_context(chat_id, date DESC)
                """
            )
            # FTS5 table for keyword retrieval while retaining base table source-of-truth.
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS telegram_context_fts
                USING fts5(message, chat_title, content='telegram_context', content_rowid='id')
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS telegram_context_ai
                AFTER INSERT ON telegram_context
                BEGIN
                    INSERT INTO telegram_context_fts(rowid, message, chat_title)
                    VALUES (new.id, new.message, COALESCE(new.chat_title, ''));
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS telegram_context_ad
                AFTER DELETE ON telegram_context
                BEGIN
                    INSERT INTO telegram_context_fts(telegram_context_fts, rowid, message, chat_title)
                    VALUES('delete', old.id, old.message, COALESCE(old.chat_title, ''));
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS telegram_context_au
                AFTER UPDATE ON telegram_context
                BEGIN
                    INSERT INTO telegram_context_fts(telegram_context_fts, rowid, message, chat_title)
                    VALUES('delete', old.id, old.message, COALESCE(old.chat_title, ''));
                    INSERT INTO telegram_context_fts(rowid, message, chat_title)
                    VALUES (new.id, new.message, COALESCE(new.chat_title, ''));
                END
                """
            )

    def ingest_context(self, row: ContextRow) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_context (
                    chat_id, message_id, sender_id, chat_title, message, source, date
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.chat_id,
                    row.message_id,
                    row.sender_id,
                    row.chat_title,
                    row.message,
                    row.source,
                    row.date,
                ),
            )
            return cursor.rowcount > 0

    def retrieve_context(
        self,
        *,
        chat_id: int,
        query: str,
        limit: int,
        since: str | None,
    ) -> list[sqlite3.Row]:
        query = query.strip()
        fetch_limit = max(limit * 5, 20)

        with self._connect() as conn:
            base_params: list[object] = [chat_id]
            where = ["tc.chat_id = ?"]
            if since:
                where.append("tc.date >= ?")
                base_params.append(since)

            if query:
                sql = f"""
                    SELECT tc.chat_id, tc.message_id, tc.sender_id, tc.chat_title, tc.message, tc.source, tc.date
                    FROM telegram_context_fts f
                    JOIN telegram_context tc ON tc.id = f.rowid
                    WHERE {' AND '.join(where)} AND telegram_context_fts MATCH ?
                    ORDER BY tc.date DESC
                    LIMIT ?
                """
                fts_params = [*base_params, query, fetch_limit]
                rows = conn.execute(sql, fts_params).fetchall()
                if rows:
                    return rows

            sql = f"""
                SELECT tc.chat_id, tc.message_id, tc.sender_id, tc.chat_title, tc.message, tc.source, tc.date
                FROM telegram_context tc
                WHERE {' AND '.join(where)}
                ORDER BY tc.date DESC
                LIMIT ?
            """
            fallback_params = [*base_params, fetch_limit]
            return conn.execute(sql, fallback_params).fetchall()
