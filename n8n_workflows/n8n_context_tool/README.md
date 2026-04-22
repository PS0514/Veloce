# n8n Context Tool Workflows

This folder contains importable n8n workflow templates for:
1. Storing Telegram listener payloads as durable context.
2. Exposing a retrieval webhook that your AI agent can call as a tool.

## Files

- `01_ingest_telegram_context.workflow.json`
- `02_context_tool_retrieval.workflow.json`
- `context_tool_contract.json`
- `sqlite_schema.sql`

## Setup

1. Import both workflow JSON files into n8n.
2. Configure the SQLite node credentials in both workflows.
3. Run the SQL in `sqlite_schema.sql` once (or let the ingest workflow create the table).
4. Point listener webhook (`N8N_WEBHOOK_URL`) to the ingest endpoint path.
5. Add the retrieval endpoint as an HTTP tool inside your AI Agent workflow.

## Endpoints

- Ingest endpoint: `POST /telegram-context-ingest`
- Retrieval endpoint: `POST /telegram-context-retrieve`

## Listener Mapping

Your listener now sends fields like:
- `source`
- `message_id`
- `sender_id`
- `chat_id`
- `chat_title`
- `message`
- `date`

These map directly to the `telegram_context` table.

## AI Tool Usage (recommended)

Tool name: `get_telegram_context`

Request body example:
```json
{
  "chat_id": -1001234567890,
  "query": "deadline project presentation",
  "limit": 8,
  "since": "2026-04-01T00:00:00Z"
}
```

Response shape:
- `chat_id`: number
- `query`: string
- `returned`: number
- `items`: array of `{ message_id, sender_id, chat_title, message, date, source, score }`

## Notes

- Dedupe is handled by unique key `(chat_id, message_id)`.
- Startup history replay on listener restarts is safe because duplicates are ignored.
- Keep `limit` small (5-10) to control token usage in AI calls.
