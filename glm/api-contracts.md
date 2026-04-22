# API Contract: GLM Reasoning Layer

## Endpoint (via n8n)
`POST /webhooks/extract-task`

## Request Payload (from Chrome Extension)
| Field | Type | Description |
| :--- | :--- | :--- |
| `raw_text` | String | The scraped text from Gmail/Moodle/Telegram |
| `source` | String | 'gmail', 'moodle', or 'telegram' |
| `user_now` | DateTime | Current timestamp of the user for relative date parsing |

## Success Response (from GLM)
Returns the JSON defined in `/glm/schema.json`.

## Error Handling
- **400:** Invalid JSON structure.
- **422:** Z.AI could not identify any entities (Empty `entities` array).

## Telegram Context Tool (n8n)

### Ingest Endpoint
`POST /telegram-context-ingest`

Used by the Telegram listener to persist inbound messages for later AI retrieval.

### Retrieval Endpoint
`POST /telegram-context-retrieve`

Request fields:
- `chat_id` (required, number)
- `query` (optional, string)
- `limit` (optional, number, default 8)
- `since` (optional, ISO date-time)

Returns ranked context snippets for AI tool usage.