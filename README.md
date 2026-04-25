# 🚀 Veloce: The Agentic Productivity Orchestrator

**UMHackathon 2026 Submission**

Veloce is a high-performance, multi-agent orchestration system that bridges the gap between messy digital communication and structured productivity. It doesn't just "read" your messages; it **strategizes** your life.

---

## 🌟 The "Secret Sauce" (What makes Veloce different?)

Unlike standard "GPT-wrappers," Veloce implements advanced engineering patterns to handle the unpredictability of human workflows:

*   **⚡ Energy-Aware Scheduling:** Veloce understands that not all hours are equal. It prioritizes "Deep Work" (coding, writing) during your peak energy windows and "Shallow Work" (emails, admin) during your slumps.
*   **🧠 Historical Bias Correction:** Veloce learns your "planning fallacy." If you consistently underestimate how long a task takes, Veloce automatically applies a multiplier to your future estimates based on your actual performance history.
*   **🔗 Omnichannel Ingestion:** Native integration with **Telegram (Userbot & Bot)**, **Gmail (Polling)**, and **Moodle/Spectrum (Scraping)** ensures no deadline is missed, regardless of where it originates.
*   **🤖 Multi-Agent Pipeline:**
    *   **The Extractor:** Parses unstructured text into structured parameters.
    *   **The Strategist:** Decomposes tasks, adjusts durations based on bias, and selects optimal energy windows.
    *   **The Validator:** Hard-checks Google Calendar for deterministic conflict resolution.

---

## 🛠️ Architecture: Microservices Approach

Veloce is built as a decoupled, event-driven ecosystem using **Docker Compose**:

1.  **`orchestrator`**: The "Brain" (FastAPI). Manages the logic flow and database.
2.  **`telegram`**: A dual-mode service (Userbot for listening, Bot for notifications).
3.  **`gmail`**: A polling microservice that monitors your inbox via OAuth2.
4.  **`glm`**: A dedicated LLM proxy service for `ilmu-glm-5.1`.
5.  **`calendar`**: The Google Calendar integration layer.
6.  **`Chrome Extension`**: A lightweight scraper for Moodle and manual browser-based triggers.

---

## 🚀 Quickstart

### 1. Requirements
*   Python 3.10+
*   Docker & Docker Compose
*   Google Cloud Console Project (with Calendar & Gmail APIs enabled)
*   Telegram API ID/Hash

### 2. Installation
```bash
# Clone and install dependencies
git clone https://github.com/your-repo/veloce
pip install -r requirements.txt

# Setup environment
cp .env.example .env
```

### 3. The Setup Wizard (Recommended)
Veloce includes a custom-built, interactive **Setup Wizard** to handle the complex OAuth and Telegram handshakes:
```bash
python scripts/run_setup.py
```
*   Visit `http://127.0.0.1:8765`
*   Connect Telegram (scan QR/Code)
*   Connect Google (OAuth)
*   Configure **Energy Windows** (Deep/Shallow work hours)

### 4. Deploy
```bash
docker compose -f deploy/docker-compose.yaml up -d
```

---

## 📖 Feature Deep Dive

### 🌖 Proactive Morning Briefs
Every morning at 8:00 AM, Veloce analyzes your day and sends a Telegram summary. It doesn't just list events; it highlights conflicts and asks for feedback on yesterday's tasks to refine your **Historical Bias** data.

### 🧩 Chrome Extension (LMS Integration)
Specifically designed for UM students, the extension monitors **Moodle/Spectrum**. When you view a course page or forum post, Veloce extracts assignment deadlines automatically and proposes them in your Telegram chat for a "One-Click Schedule."

### ⚖️ Active Ambiguity Resolution
If you say "Sync tomorrow," and you have multiple projects, Veloce won't guess. It triggers a **Clarification State**, asking: *"Which project are we syncing? (A) Website (B) PRD Update"*.

---

## 🛡️ Privacy & Security
*   **Stateful Local Store:** All context is stored in a local SQLite database, not on external servers.
*   **Selective Tracking:** Only the Telegram channels you explicitly "Opt-In" to are monitored.
*   **Token Rotation:** Automatic management and secure rotation of Google OAuth refresh tokens.
