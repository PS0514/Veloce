# Veloce Core n8n Workflows

This folder is the source-of-truth for importable n8n workflow templates used in backend orchestration.

## Files

- `03_core_task_scheduler.workflow.json`

## What the Core Workflow Does

Pipeline implemented in `03_core_task_scheduler.workflow.json`:

1. Ingestion: receives Telegram listener payload on `POST /veloce-task-scheduler`
2. Reasoning: calls Z.AI GLM and extracts strict JSON with:
   - `task_name`
   - `deadline_iso`
   - `estimated_duration_minutes`
3. Constraint checking: fetches Google Calendar events between now and deadline, then computes the earliest free slot.
4. Execution: creates a Google Calendar event for the selected slot.
5. Notification: sends confirmation to the user through Telegram Bot API.

## Expected Inbound Payload

Compatible with current listener output:

```json
{
  "source": "telegram_userbot",
  "message_id": 123,
  "sender_id": 456,
  "chat_id": -1001234567890,
  "chat_title": "Course Group",
  "message": "Project report due this Friday 5pm",
  "date": "2026-04-22T10:00:00+08:00"
}
```

Also supports `raw_text` as an alternative to `message`.

## Required n8n Setup

1. Import workflow JSON into n8n.
2. Add OAuth2 credential for Google APIs and assign it to:
   - `List Calendar Events`
   - `Create Calendar Event`
3. Ensure `.env` has:
   - `ZAI_API_KEY`
   - `ZAI_MODEL` (optional, default `glm-4.5`)
   - `ZAI_CHAT_COMPLETIONS_URL` (optional OpenAI-compatible endpoint)
   - `TELEGRAM_BOT_TOKEN`
   - `GENERIC_TIMEZONE` (recommended `Asia/Kuala_Lumpur`)
4. Set listener target URL (`N8N_WEBHOOK_URL`) to this workflow path:
   - `http://n8n:5678/webhook/veloce-task-scheduler` (inside Docker network)
   - `http://localhost:5678/webhook/veloce-task-scheduler` (local testing)

## Build Order in n8n UI (Node-by-Node)

If you want to rebuild manually instead of importing JSON:

1. Webhook node: `Webhook Core Ingest`
2. Code node: `Normalize Inbound`
3. Code node: `Build GLM Request`
4. HTTP Request node: `Call ZAI GLM`
5. Code node: `Parse GLM Output`
6. IF node: `Has Task?`
7. Code node: `Prepare Calendar Window`
8. HTTP Request node: `List Calendar Events`
9. Code node: `Find Free Slot`
10. IF node: `Slot Found?`
11. HTTP Request node: `Create Calendar Event`
12. Code node: `Build Notification`
13. HTTP Request node: `Send Telegram Confirmation`
14. Respond to Webhook nodes:
    - `Respond No Task`
    - `Respond No Slot`
    - `Respond Success`

## Validation Checklist

- Webhook receives payload from listener with `message` or `raw_text`.
- GLM response is valid JSON and contains `tasks` array.
- First task with nearest deadline is selected.
- Calendar event is created only when slot is found before deadline.
- Telegram message posts to the same chat via `chat_id`.
- Webhook response returns structured success/failure JSON.

## Notes

- This workflow intentionally schedules one task per inbound message (earliest deadline task).
- For batch scheduling across all extracted tasks, add a `Split Out` loop after `Parse GLM Output`.
- Keep your existing context-tool workflows in `glm/n8n_context_tool` active for retrieval augmentation.
