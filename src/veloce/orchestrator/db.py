import sqlite3
from dataclasses import dataclass
from pathlib import Path

from veloce.orchestrator.logging_utils import get_logger, log_info

logger = get_logger(__name__)


@dataclass(frozen=True)
class ContextRow:
    chat_id: int
    message_id: int
    sender_id: int | None
    chat_title: str | None
    message: str
    source: str
    date: str | None


@dataclass(frozen=True)
class ScheduledTaskRow:
    task_name: str
    start_time: str
    end_time: str
    calendar_event_id: str | None
    chat_id: int | None
    message_id: int | None


@dataclass(frozen=True)
class AutomatedMessageRow:
    chat_id: int
    message_id: int
    bot_type: str  # 'userbot' or 'fatherbot'
    trigger_msg_id: int | None = None
    task_name: str | None = None


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        log_info(logger, "db_store_init", db_path=self.db_path)
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
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    calendar_event_id TEXT,
                    chat_id INTEGER,
                    message_id INTEGER,
                    actual_duration_minutes INTEGER,
                    is_completed INTEGER DEFAULT 0,
                    inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Migration: Ensure columns exist for feedback loop
            try:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN actual_duration_minutes INTEGER;")
            except sqlite3.OperationalError:
                pass # Already exists
            try:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN is_completed INTEGER DEFAULT 0;")
            except sqlite3.OperationalError:
                pass # Already exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS automated_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    bot_type TEXT NOT NULL,
                    trigger_msg_id INTEGER,
                    task_name TEXT,
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_start
                ON scheduled_tasks(start_time DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automated_messages_lookup
                ON automated_messages(chat_id, message_id)
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
            log_info(logger, "db_store_ready", db_path=self.db_path)

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
            inserted = cursor.rowcount > 0
            log_info(
                logger,
                "db_ingest_context",
                chat_id=row.chat_id,
                message_id=row.message_id,
                inserted=inserted,
                source=row.source,
            )
            return inserted

    def ingest_scheduled_task(self, row: ScheduledTaskRow) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    task_name, start_time, end_time, calendar_event_id, chat_id, message_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.task_name,
                    row.start_time,
                    row.end_time,
                    row.calendar_event_id,
                    row.chat_id,
                    row.message_id,
                ),
            )
            log_info(
                logger,
                "db_ingest_scheduled_task",
                task_name=row.task_name,
                start_time=row.start_time,
            )
            return cursor.rowcount > 0

    def ingest_automated_message(self, row: AutomatedMessageRow) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO automated_messages (
                    chat_id, message_id, bot_type, trigger_msg_id, task_name
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row.chat_id,
                    row.message_id,
                    row.bot_type,
                    row.trigger_msg_id,
                    row.task_name,
                ),
            )
            inserted = cursor.rowcount > 0
            log_info(
                logger,
                "db_ingest_automated_message",
                chat_id=row.chat_id,
                message_id=row.message_id,
                bot_type=row.bot_type,
                inserted=inserted,
            )
            return inserted

    def is_automated_message(self, chat_id: int, message_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM automated_messages WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            return row is not None

    def retrieve_trigger_id(self, chat_id: int, message_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT trigger_msg_id FROM automated_messages WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            return row["trigger_msg_id"] if row else None

    def retrieve_message(self, chat_id: int, message_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT chat_id, message_id, sender_id, chat_title, message, source, date FROM telegram_context WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()

    def retrieve_scheduled_tasks(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT task_name, start_time, end_time, calendar_event_id, chat_id, message_id
                FROM scheduled_tasks
                ORDER BY start_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def retrieve_chat_id_by_title(self, title: str) -> int | None:
        if not title:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT chat_id FROM telegram_context WHERE chat_title = ? LIMIT 1",
                (title,),
            ).fetchone()
            return row["chat_id"] if row else None

    def update_task_feedback(self, calendar_event_id: str, actual_duration_minutes: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks 
                SET actual_duration_minutes = ?, is_completed = 1 
                WHERE calendar_event_id = ?
                """,
                (actual_duration_minutes, calendar_event_id),
            )
            log_info(
                logger,
                "db_update_task_feedback",
                calendar_event_id=calendar_event_id,
                actual_duration=actual_duration_minutes,
                found=cursor.rowcount > 0
            )
            return cursor.rowcount > 0

    def calculate_historical_bias(self) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_name, start_time, end_time, actual_duration_minutes 
                FROM scheduled_tasks 
                WHERE is_completed = 1 AND actual_duration_minutes IS NOT NULL
                ORDER BY inserted_at DESC LIMIT 50
                """
            ).fetchall()
            
            if not rows:
                return "No historical data yet."
            
            diffs = []
            for r in rows:
                try:
                    start = datetime.fromisoformat(r["start_time"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(r["end_time"].replace("Z", "+00:00"))
                    estimated = int((end - start).total_seconds() / 60)
                    actual = r["actual_duration_minutes"]
                    if estimated > 0:
                        diffs.append(actual / estimated)
                except Exception:
                    continue
            
            if not diffs:
                return "No valid historical comparisons found."
                
            avg_multiplier = sum(diffs) / len(diffs)
            return f"Historical Performance: User typically takes {round(avg_multiplier, 2)}x the estimated time (based on {len(diffs)} tasks)."

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

        log_info(
            logger,
            "db_retrieve_context_start",
            chat_id=chat_id,
            query=query,
            limit=limit,
            since=since,
            fetch_limit=fetch_limit,
        )

        with self._connect() as conn:
            base_params: list[object] = [chat_id]
            where = ["tc.chat_id = ?"]
            if since:
                where.append("tc.date >= ?")
                base_params.append(since)

            safe_query = f'"{query}"' if query else ""

            if query:
                sql = f"""
                    SELECT tc.chat_id, tc.message_id, tc.sender_id, tc.chat_title, tc.message, tc.source, tc.date
                    FROM telegram_context_fts f
                    JOIN telegram_context tc ON tc.id = f.rowid
                    WHERE {' AND '.join(where)} AND telegram_context_fts MATCH ?
                    ORDER BY tc.date DESC
                    LIMIT ?
                """
                fts_params = [*base_params, safe_query, fetch_limit]
                rows = conn.execute(sql, fts_params).fetchall()
                if rows:
                    log_info(
                        logger,
                        "db_retrieve_context_done",
                        chat_id=chat_id,
                        mode="fts",
                        rows=len(rows),
                    )
                    return rows

            sql = f"""
                SELECT tc.chat_id, tc.message_id, tc.sender_id, tc.chat_title, tc.message, tc.source, tc.date
                FROM telegram_context tc
                WHERE {' AND '.join(where)}
                ORDER BY tc.date DESC
                LIMIT ?
            """
            fallback_params = [*base_params, fetch_limit]
            rows = conn.execute(sql, fallback_params).fetchall()
            log_info(
                logger,
                "db_retrieve_context_done",
                chat_id=chat_id,
                mode="fallback",
                rows=len(rows),
            )
            return rows
