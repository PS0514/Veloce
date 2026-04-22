# Veloce

AI-powered workflow automation system that transforms unstructured messages into structured tasks and automatically schedules them into Google Calendar.

---

# 🚀 Problem

Users receive important information across Gmail, Telegram, and Moodle, but:
- Tasks are scattered
- Deadlines are missed
- Manual scheduling is inefficient

---

# 💡 Solution

Veloce automates the entire workflow:

- Reads unstructured messages (email, chat, LMS)
- Uses Z.AI GLM to understand intent
- Extracts tasks, meetings, and deadlines
- Automatically schedules into Google Calendar
- Detects conflicts and suggests better time slots

---

# ⚙️ Tech Stack

- 🧠 Z.AI GLM → AI reasoning engine
- ⚙️ n8n → workflow orchestration backend
- 🧩 Chrome Extension → data extraction + UI
- 📅 Google Calendar API → scheduling system

---

# 🔁 System Flow

Chrome Extension  
→ n8n Webhook  
→ Z.AI GLM (reasoning)  
→ n8n workflow  
→ Google Calendar  
→ Chrome Extension UI

---

# ✨ Key Features

- Smart task extraction from messages
- Auto calendar scheduling
- Conflict detection
- AI-driven decision making
- Personal preference awareness 

---

# Quickstart

1. Install dependencies:
	- `pip install -r requirements.txt`
2. Run setup wizard:
	- `python scripts/run_setup.py`
3. In the setup page:
	- Telegram API ID/API Hash auto-fill from `.env` if already present.
	- If you do not have Telegram API credentials yet, create them at `https://my.telegram.org`:
	  - Log in with your phone number.
	  - Open **API development tools**.
	  - Create an app (name + short name).
	  - Copy **api_id** and **api_hash** into the wizard.
	- Click **Save configuration** once with **Run Telegram login now** enabled to authorize `veloce_session`.
	- Click **List my channels** to fetch your Telegram chats/channels.
	- Use search + sort (**Latest message** or **Name**) to find channels quickly, then click **Use selected channels** to populate the filter field.
	- **Keywords are optional**: leave keywords empty to process all messages from selected channels.
4. Run listener manually:
	- `python scripts/run_listener.py`
5. Or run with Docker compose:
	- `docker compose -f deploy/docker-compose.yaml up -d`

Compatibility shortcuts still available:
- `python setup.py`
- `python listener.py`

---


# 🏆 Impact

- Reduces manual effort in planning and scheduling by automating task creation from unstructured messages
- Converts emails, chats, and LMS notifications into structured calendar events
- Minimizes missed deadlines and important meetings through intelligent reminders
- Improves time management by automatically organizing user schedules
- Reduces cognitive load by eliminating the need to manually interpret and input tasks
- Prevents scheduling conflicts through AI-driven conflict detection
- Enhances productivity by streamlining communication-to-action workflow

---