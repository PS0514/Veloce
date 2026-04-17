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


# 🏆 Impact

- Reduces manual effort in planning and scheduling by automating task creation from unstructured messages
- Converts emails, chats, and LMS notifications into structured calendar events
- Minimizes missed deadlines and important meetings through intelligent reminders
- Improves time management by automatically organizing user schedules
- Reduces cognitive load by eliminating the need to manually interpret and input tasks
- Prevents scheduling conflicts through AI-driven conflict detection
- Enhances productivity by streamlining communication-to-action workflow

---