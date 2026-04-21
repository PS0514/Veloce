# GLM Reasoning Pipeline Rules

## 1. Ambiguity Handling (The "Safety Net")
- **Rule:** If the input time is "Morning", "Afternoon", or "Evening" without specific hours:
  - **Action:** Assign default values (Morning: 09:00, Afternoon: 14:00, Evening: 20:00).
  - **Flag:** `is_prediction = true` AND `needs_clarification = true`.

## 2. Multi-Task Splitting (The "Separator")
- **Logic:** The GLM must look for semantic breaks:
  - Connectors: "and", "then", "after that", "also".
  - Temporal shifts: "at 2pm... also by midnight".
- **Execution:** Each break MUST trigger a new object in the `entities` array.

## 3. Conflict Detection (The "Preference Awareness")
- **Logic:** If `start_time` matches a `busy_slot` (provided by Person C from Google Calendar):
  - **Action:** Do NOT change the time.
  - **Flag:** `needs_clarification = true`.
  - **Clarification:** "This overlaps with your [Event Name]. Move it?"