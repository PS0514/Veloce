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