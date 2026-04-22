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
7. Or run both with Docker:
   - `docker compose -f deploy/docker-compose.yaml up -d`

Compatibility launchers still work:

- `python setup.py`
- `python listener.py`

## Required Environment Variables

Core:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `VELOCE_ORCHESTRATOR_URL` (example: `http://127.0.0.1:8000/veloce-task-scheduler`)
- `GENERIC_TIMEZONE` (example: `Asia/Kuala_Lumpur`)
- `GOOGLE_CALENDAR_ID` (calendar selected in the wizard)

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

## Browser Google Login

The setup wizard now supports a browser-based OAuth flow for Google.

1. Open the setup wizard.
2. In the Google Calendar section, enter your Google Client ID and Client Secret.
3. Add this redirect URI in Google Cloud Console: `http://127.0.0.1:8765/google/oauth/callback`
4. Click `Sign in with Google` in the wizard.
5. Finish consent in the browser.
6. Return to the wizard, click `List my Google calendars`, and choose the target calendar.
7. Save the configuration.

## Google Calendar Setup Guide

1. Open [Google Calendar](https://calendar.google.com) and sign in.
2. In the left sidebar, click the `+` next to `Other calendars`.
3. Choose `Create new calendar`.
4. Give the calendar a clear name, such as `Veloce` or `Study Planner`.
5. Click `Create calendar` and confirm it appears in your calendar list.
6. Return to the setup wizard, click `List my Google calendars`, and select the calendar you want Veloce to use.
7. Save the configuration so `GOOGLE_CALENDAR_ID` is written to `.env`.

If you want Veloce to schedule into your main calendar, leave `GOOGLE_CALENDAR_ID=primary`.

Context DB:

- `VELOCE_DB_PATH` (optional, default `data/veloce.db`)

## Notes

- If Google sync is disabled, scheduler returns a non-scheduled decision with reason.
- If GLM cannot extract a valid actionable task, scheduler returns no-action.
- Docker compose now starts the Python orchestrator service plus the Telegram listener.
- n8n workflow JSON files are retained under [n8n_workflows/](n8n_workflows/) as legacy references during migration.