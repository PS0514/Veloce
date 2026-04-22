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
);

CREATE INDEX IF NOT EXISTS idx_telegram_context_chat_date
ON telegram_context(chat_id, date DESC);
