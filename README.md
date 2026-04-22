# Veloce

Veloce is a Python-first AI orchestration system that turns unstructured messages into actionable tasks and schedules them into Google Calendar.

## Problem

Important task and deadline info is scattered across Telegram, Gmail, and LMS feeds. Manual triage and calendar entry is slow and error-prone.

## Solution

Veloce now runs orchestration fully in Python:

1. Receive inbound messages (listener or extension webhook).
2. Use GLM as the reasoning engine to interpret and extract tasks.
3. Decide among no-action, needs-context, needs-clarification, or schedule-now.
4. Query Google Calendar availability.
5. Check clashes and find earliest free slot before deadline.
6. Create calendar event and return scheduling result.

## Current Architecture

- AI: Z.AI GLM (OpenAI-compatible chat completions).
- Orchestration API: FastAPI service in [src/veloce/orchestrator/app.py](src/veloce/orchestrator/app.py).
- Listener: Telethon listener in [src/veloce/listener_service.py](src/veloce/listener_service.py).
- Context store: SQLite + FTS5 in [src/veloce/orchestrator/db.py](src/veloce/orchestrator/db.py).
- Scheduling: Calendar adapter and slot engine in [src/veloce/orchestrator/scheduling_engine.py](src/veloce/orchestrator/scheduling_engine.py).

## API Endpoints

- POST `/veloce-task-scheduler`
- POST `/telegram-context-ingest`
- POST `/telegram-context-retrieve`
- GET `/health`

## Quickstart

1. Create and activate a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set required values.
4. Run setup wizard (optional but recommended):
   - `python scripts/run_setup.py`
5. Run orchestrator API:
   - `python scripts/run_orchestrator.py`
6. Run Telegram listener:
   - `python scripts/run_listener.py`

Compatibility launchers still work:

- `python setup.py`
- `python listener.py`

## Required Environment Variables

Core:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `VELOCE_ORCHESTRATOR_URL` (example: `http://127.0.0.1:8000/veloce-task-scheduler`)
- `GENERIC_TIMEZONE` (example: `Asia/Kuala_Lumpur`)

GLM:

- `ZAI_API_KEY`
- `ZAI_CHAT_COMPLETIONS_URL`
- `ZAI_MODEL` (optional, default `glm-4.5`)

Google Calendar scheduling:

- `ENABLE_GOOGLE_SYNC=true`
- `GOOGLE_CALENDAR_ID` (optional, default `primary`)
- One auth mode:
  - Access token mode: `GOOGLE_ACCESS_TOKEN`
  - Refresh mode: `GOOGLE_REFRESH_TOKEN` + `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`

Context DB:

- `VELOCE_DB_PATH` (optional, default `data/veloce.db`)

## Notes

- If Google sync is disabled, scheduler returns a non-scheduled decision with reason.
- If GLM cannot extract a valid actionable task, scheduler returns no-action.
- n8n workflow JSON files are retained under [n8n_workflows/](n8n_workflows/) as legacy references during migration.