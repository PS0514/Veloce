# Veloce Rules Engine

The Veloce Orchestrator uses a multi-stage AI reasoning pipeline to process inbound messages. Each stage follows specific rules defined in the system prompts and Python logic.

## 1. Intent Classification
The system first determines the user's intent to decide which handler to use.
- **`schedule_task`:** Default for anything task-like or confirming a previous proposal.
- **`query_calendar`:** Triggered by questions about availability or upcoming events.
- **`save_memory`:** For personal facts or preferences the user wants Veloce to remember.
- **`general_chat`:** Greetings, small talk, or unrelated conversation.

## 2. Extraction Rules
When a task is identified, the extraction engine follows these mandates:
- **Relative Date Resolution:** Uses message timestamps and recent chat history to resolve words like "tomorrow", "next Friday", or "later".
- **Deduplication:** Checks against the database of already scheduled tasks to avoid duplicates.
- **Ambiguity Handling:** If a task is optional or lacks specific details (e.g., "Lunch sometime"), it sets `needs_clarification = true` and generates a question for the user.
- **Confirmation Awareness:** If the user says "Yes" to a bot's question, the engine looks back at the context to re-extract the task details.

## 3. Strategist Agent (Multi-Agent Layer)
For complex tasks, the Strategist Agent applies high-level planning:
- **Task Decomposition:** 
  - **Assignments:** Broken into [Research, Drafting, Final Polish].
  - **Exams:** Broken into [Active Recall Session 1, Active Recall Session 2, Mock Exam].
  - **Simple Events:** Social gatherings or doctor appointments are NOT decomposed.
- **Planning Fallacy Buffer:** Academic assignments automatically get a 1.5x duration multiplier.
- **Workload Awareness:** If the user has >3 major deadlines in a week, the Strategist spaces out support sessions.

## 4. Scheduling Logic
The final step before event creation:
- **Energy-Aware Windows:**
  - **Deep Work (09:00 - 13:00):** Reserved for high-energy tasks (Research, Problem Sets).
  - **Shallow Work (14:00 - 17:00):** Used for low-energy tasks (Admin, Formatting, Social).
- **Historical Bias:** The system calculates a bias factor from previous tasks (e.g., if a user consistently takes 20% longer than estimated) and adjusts proposed durations.
- **Conflict Management:** 
  - If a proposed slot overlaps with an existing calendar event, the system flags it for clarification.
  - Ephemeral Memory: Within a single batch processing, the system "remembers" tasks it just scheduled to avoid scheduling sub-tasks on top of each other.

## 5. Feedback Loop
- **Task Completion:** Users can provide feedback (e.g., "History Essay took 60m").
- **Learning:** This feedback is stored in `scheduled_tasks` and used to update the "Historical Bias" provided to the Strategist Agent in future interactions.
- **Memory Integration:** User preferences saved via `save_memory` (e.g., "I study better at night") are injected into the Strategist's context to customize planning.
