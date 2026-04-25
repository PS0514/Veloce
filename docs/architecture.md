# Veloce Architecture

Veloce is a modular AI orchestration system designed to capture tasks from various sources (Telegram, Browser, Gmail) and schedule them into Google Calendar with AI-driven reasoning.

## System Components

### 1. Orchestrator (`src/veloce/orchestrator/`)
The central FastAPI service that coordinates all logic. It handles:
- **Pipeline Execution:** Intent classification -> Task extraction -> Strategy decomposition -> Scheduling.
- **Context Management:** Stores message history in SQLite (with FTS5) for semantic retrieval.
- **Background Tasks:** Daily brief generation and delivery.
- **Microservice Coordination:** Communicates with specialized services for Calendar, GLM, Gmail, and Telegram.

### 2. Microservices (`src/veloce/services/`)
Specialized FastAPI services that wrap external APIs:
- **Telegram Service:** A Telethon-based userbot that monitors chats and sends messages to the Orchestrator.
- **Calendar Service:** Manages Google Calendar interactions (OAuth, event creation, availability checks).
- **GLM Service:** Interface for the LLM (Z.AI / Ilmu GLM) for reasoning and extraction.
- **Gmail Service:** Monitors emails for task-related content.

### 3. Setup Wizard (`src/veloce/setup_wizard.py`)
A Flask-based local web UI for managing configuration:
- Environment variable management (`.env`).
- Google OAuth flow.
- Telegram authentication (Phone/2FA).
- Runtime configuration (Energy windows, keywords).

### 4. Chrome Extension (`chrome-extension/`)
Allows users to highlight text on any webpage and send it directly to the Orchestrator for scheduling.

## Current Layout

```text
src/
  veloce/
    orchestrator/     # Central reasoning & API
    services/
      calendar/       # Google Calendar wrapper
      glm/            # LLM interface
      gmail/          # Email monitor
      telegram/       # Userbot listener
    setup_wizard.py   # Configuration UI
scripts/              # Entry points for all services
deploy/               # Docker configs for microservices
docs/                 # Documentation
glm/
  prompt/             # AI system prompts (The "Brains")
```

## Data Flow

1. **Capture:** A message arrives via Telegram, an email is received, or a user sends text from the Chrome extension.
2. **Ingest:** The source service sends the raw data to the Orchestrator's `/telegram-context-ingest` or `/veloce-task-scheduler`.
3. **Reason:**
   - **Intent Classification:** Is this a task, a query, a memory, or just chat?
   - **Extraction:** If it's a task, extract the name, deadline, and duration. Use context history for relative dates.
   - **Strategizing:** For complex tasks (e.g., "Exam"), decompose into sub-tasks (Study sessions).
4. **Schedule:** Check availability via the Calendar Service, apply energy-window preferences, and create Google Calendar events.
5. **Feedback:** User interacts with the scheduled tasks, and the system learns from historical bias (e.g., "User usually takes 20% longer").

## Configuration

- **Static:** `.env` file (API keys, secrets).
- **Dynamic:** `data/veloce_config.json` managed via the Setup Wizard.
- **Context:** `data/veloce.db` (SQLite store for messages and scheduled tasks).
