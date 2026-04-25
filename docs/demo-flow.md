# Veloce Demo Flow

This document outlines the standard "Happy Path" for a user interacting with Veloce via Telegram.

## Scenario: Planning for an Exam

### 1. Initial Capture
**User (Telegram Group):** "I have a Physics exam next Tuesday at 2pm."

**Veloce Logic:**
- **Intent Classifier:** Identified as `schedule_task`.
- **Extraction Engine:** 
  - Resolves "next Tuesday" based on current date.
  - Extracts `Physics Exam` at `14:00`.
  - Sets `estimated_duration_minutes` to 120 (default for exams).
- **Strategist Agent:** 
  - Recognizes "Exam" as a complex task.
  - Decomposes into:
    1. Physics Review 1 (90m, Deep Work)
    2. Physics Review 2 (90m, Deep Work)
    3. Mock Exam Session (120m, Deep Work)
- **Orchestrator:** Sees multiple tasks generated. Instead of scheduling directly, it generates a proposal.

### 2. Proposal & Approval
**Veloce (Telegram):** 
"I've created a study plan for your Physics exam:
- Physics Review 1: Monday at 09:00
- Physics Review 2: Monday at 11:30
- Mock Exam Session: Tuesday at 09:00
- Physics Exam: Tuesday at 14:00

Would you like me to proceed with scheduling these 4 events?"

**User:** "Yes, go ahead."

**Veloce Logic:**
- **Intent Classifier:** `schedule_task` (Confirmation).
- **Extraction Engine:** Looks back at context, sees the proposed plan, and marks it as ready to schedule.
- **Scheduling Engine:** 
  - Checks Google Calendar for conflicts.
  - Successfully creates 4 events.

**Veloce (Telegram):** "Done! I've added the study sessions and the exam to your calendar. [Link to Calendar]"

### 3. Contextual Adjustment (The Browser)
**User (Chrome Extension):** *Highlights a syllabus on a website with "Lab 3 due Thursday"* -> *Clicks "Add to Veloce"*

**Veloce Logic:**
- **Inbound:** Sent via Chrome Extension to `/veloce-manual-calendar-add`.
- **Orchestrator:** Recognizes manual highlight. Extracts `Lab 3` at `Thursday`.
- **Scheduling Engine:** Directly adds to calendar.

### 4. Daily Briefing
**Veloce (Telegram - 08:00 AM Next Day):**
"Good morning! Here is your plan for today:
- 09:00 AM: Physics Review 1 (Deep Work)
- 11:30 AM: Physics Review 2 (Deep Work)

You have a Lab 3 deadline coming up on Thursday. Good luck!"

### 5. Feedback Loop
**User (Telegram):** "Physics Review 1 took 120m"

**Veloce Logic:**
- **Regex Match:** "took [X]m" pattern detected.
- **Database:** Updates the `actual_duration_minutes` for the most recent "Physics Review 1" task.
- **Learning:** Future "Physics" tasks will now be estimated with a +33% bias factor for this user.
