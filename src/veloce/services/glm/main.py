import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import (
    ContextItem,
    GlmExtraction,
    NormalizedInbound,
    TaskCandidate,
)

logger = get_logger(__name__)

class _RateLimiter:
    """Thread-safe token-bucket rate limiter with a FIFO queue."""
    def __init__(self, max_rpm: int = 10) -> None:
        self._min_interval = 60.0 / max(max_rpm, 1)
        self._max_rpm = max(max_rpm, 1)
        self._lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._last_call: float = 0.0
        self._pending: int = 0

    @property
    def max_rpm(self) -> int:
        return self._max_rpm

    def acquire(self, request_id: str | None = None) -> float:
        with self._queue_lock:
            with self._lock:
                self._pending += 1
                pending = self._pending

            waited = 0.0
            while True:
                with self._lock:
                    now = time.monotonic()
                    elapsed = now - self._last_call
                    if elapsed >= self._min_interval:
                        self._last_call = now
                        self._pending -= 1
                        break
                    sleep_for = self._min_interval - elapsed
                time.sleep(sleep_for)
                waited += sleep_for
            return waited

app = FastAPI(title="Veloce GLM Service", version="0.1.0")

class ExtractRequest(BaseModel):
    inbound: NormalizedInbound
    retrieved_context: Optional[List[ContextItem]] = None
    scheduled_tasks: Optional[List[dict]] = None
    request_id: Optional[str] = None
    conflict_context: Optional[str] = None

class BriefRequest(BaseModel):
    events: List[dict]
    now_iso: str
    timezone: str

class GlmService:
    def __init__(self) -> None:
        self.api_key = os.getenv("ILMU_API_KEY", "").strip()
        self.model = os.getenv("ILMU_MODEL", "ilmu-glm-5.1").strip() or "ilmu-glm-5.1"
        self.base_url = os.getenv("ILMU_BASE_URL", "https://api.ilmu.ai/v1").strip() or "https://api.ilmu.ai/v1"
        rpm = int(os.getenv("ILMU_RATE_LIMIT_RPM", "10") or "10")

        self._rate_limiter = _RateLimiter(max_rpm=rpm)
        self._client: OpenAI | None = None

        if self.api_key:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=180.0,
            )
        
        log_info(
            logger,
            "glm_service_init",
            model=self.model,
            rate_limit_rpm=self._rate_limiter.max_rpm,
        )

    def extract_tasks(
        self, 
        inbound: NormalizedInbound, 
        retrieved_context: Optional[List[ContextItem]] = None, 
        scheduled_tasks: Optional[List[dict]] = None,
        request_id: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> GlmExtraction:
        try:
            return self._extract_tasks_internal(inbound, retrieved_context, scheduled_tasks, request_id, conflict_context)
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            log_warning(logger, "glm_extraction_critical_error", request_id=request_id, error=str(e), traceback=error_trace)
            raise HTTPException(status_code=500, detail=f"Internal Error: {str(e)}")

    def _extract_tasks_internal(
        self, 
        inbound: NormalizedInbound, 
        retrieved_context: Optional[List[ContextItem]] = None, 
        scheduled_tasks: Optional[List[dict]] = None,
        request_id: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> GlmExtraction:
        if not self.api_key or self._client is None:
            return self._fallback_extraction(inbound, request_id=request_id)

        now = inbound.inbound_date
        base_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
        system_prompt_path = base_dir / "glm" / "prompt" /"system_prompt.txt"
        user_prompt_path = base_dir / "glm" / "prompt" / "user_prompt.txt"
        
        try:
            system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
            user_prompt_template = user_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as e:
            log_warning(logger, "missing_prompt_file", error=str(e))
            return self._fallback_extraction(inbound, request_id=request_id)
        
        context_str = ""
        if retrieved_context:
            context_lines = ["Recent Context History:"]
            sorted_context = sorted(retrieved_context, key=lambda x: x.date if x.date else "")
            for item in sorted_context:
                sender = f"User {item.sender_id}" if item.sender_id else "User"
                date_str = item.date or "Unknown time"
                context_lines.append(f"- [{date_str}] {sender}: {item.message}")
            context_lines.append("---") 
            context_str = "\n".join(context_lines)

        scheduled_str = ""
        if scheduled_tasks:
            scheduled_lines = ["Already Scheduled Tasks (DB):"]
            for task in scheduled_tasks:
                scheduled_lines.append(f"- {task['task_name']} at {task['start_time']} to {task['end_time']}")
            scheduled_lines.append("---")
            scheduled_str = "\n".join(scheduled_lines)

        # Append conflict context if provided
        raw_text = inbound.raw_text
        if conflict_context:
            raw_text += f"\n\n[Conflict Context]:\n{conflict_context}"

        # Add reply metadata to the prompt
        reply_info = ""
        if inbound.reply_to_me:
            reply_info = f"\n\n[METADATA]: The user is DIRECTLY REPLYING to your previous message (Message ID: {inbound.reply_to_msg_id}).\nOriginal message from you: \"{inbound.reply_to_text}\"\nThis is likely a response to your question above."

        user_prompt = user_prompt_template.format(
            now=now,
            timezone=inbound.timezone,
            context_history=context_str,
            scheduled_tasks=scheduled_str,
            raw_text=raw_text + reply_info
        )

        log_info(
            logger,
            "glm_payload_prepared",
            request_id=request_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context_history=context_str,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        started = time.perf_counter()
        self._rate_limiter.acquire(request_id=request_id)

        # Exponential backoff for 504/transient errors
        max_retries = 3
        retry_delay = 2.0  # start with 2 seconds
        response = None
        
        for attempt in range(max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                break # Success!
            except Exception as exc:
                is_timeout = "504" in str(exc) or "timeout" in str(exc).lower()
                if is_timeout and attempt < max_retries:
                    log_warning(
                        logger, 
                        "glm_request_retry", 
                        attempt=attempt + 1, 
                        delay=retry_delay, 
                        error=str(exc),
                        request_id=request_id
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                
                # If not a timeout or we ran out of retries, re-raise
                log_warning(logger, "glm_request_failed_final", request_id=request_id, error=str(exc))
                raise exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content = response.choices[0].message.content if response.choices else None

        if not content or not content.strip():
            log_warning(logger, "glm_empty_content", request_id=request_id)
            return GlmExtraction(tasks=[], metadata={"error": "empty_response"})

        # MANDATE: Log the full raw response content
        log_info(
            logger,
            "glm_raw_response",
            request_id=request_id,
            content=content
        )

        # Strip markdown code blocks if present
        clean_content = content.strip()
        if clean_content.startswith("```"):
            # Remove opening block
            clean_content = clean_content.lstrip("`")
            if clean_content.startswith("json"):
                clean_content = clean_content[4:].strip()
            else:
                clean_content = clean_content.strip()
            
            # Remove closing block
            if clean_content.endswith("```"):
                clean_content = clean_content[:-3].strip()

        try:
            parsed = json.loads(clean_content) if isinstance(clean_content, str) else clean_content
        except json.JSONDecodeError as jde:
            log_warning(logger, "glm_json_decode_failed", request_id=request_id, error=str(jde), raw_content=content)
            return GlmExtraction(tasks=[], metadata={"error": "json_decode_failed", "raw": content[:100]})
            
        raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        tasks: List[TaskCandidate] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            try:
                # Use 'or' to handle None values and provide sensible defaults
                # Use explicit type casting to ensure Pydantic validation passes
                tasks.append(
                    TaskCandidate(
                        task_name=str(item.get("task_name") or "Unnamed Task"),
                        deadline_iso=str(item.get("deadline_iso") or ""),
                        start_time_iso=item.get("start_time_iso"),
                        estimated_duration_minutes=int(item.get("estimated_duration_minutes") or 90),
                        confidence=float(item.get("confidence") if item.get("confidence") is not None else 0.5),
                        needs_clarification=bool(item.get("needs_clarification", False)),
                        clarification_question=item.get("clarification_question"),
                    )
                )
            except Exception as e:
                log_warning(logger, "glm_task_parse_item_error", error=str(e), item=item, request_id=request_id)
                continue

        log_info(
            logger,
            "glm_request_done",
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            tasks_found=len(tasks),
        )

        return GlmExtraction(
            tasks=tasks,
            metadata={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": self.model,
                "elapsed_ms": elapsed_ms,
            },
        )

    def _fallback_extraction(self, inbound: NormalizedInbound, request_id: Optional[str] = None) -> GlmExtraction:
        log_warning(logger, "glm_fallback", request_id=request_id, reason="missing_config")
        return GlmExtraction(tasks=[], metadata={"fallback": True})

    def generate_brief(self, events: List[dict], now_iso: str, timezone: str) -> str:
        if not self.api_key or self._client is None:
            return "Good morning! Hope you have a great day."

        # Attempt to load timezone
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(timezone)
        except Exception:
            tz = None

        event_lines = []
        for e in events:
            # e is a dict representation of CalendarEvent
            raw_start = e["start"]
            start_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            
            # Convert to local timezone if possible
            if tz and start_dt.tzinfo:
                start_dt = start_dt.astimezone(tz)
            
            # Heuristic for all-day events: Google returns 'YYYY-MM-DD' for 'date'
            # which fromisoformat turns into midnight. 
            # If the original string didn't have a 'T', it's likely an all-day event.
            if "T" not in raw_start:
                time_str = "All Day"
            else:
                time_str = start_dt.strftime("%H:%M")

            summary = e.get("summary", "Busy")
            desc = e.get("description")
            line = f"- {time_str}: {summary}"
            if desc:
                # Keep description short
                clean_desc = desc.split("\n")[0][:100]
                line += f" ({clean_desc})"
            event_lines.append(line)
        
        events_str = "\n".join(event_lines) if event_lines else "No events scheduled."
        
        system_prompt = (
            "You are Veloce, a high-performance AI task orchestrator. Your goal is to provide a warm, "
            "motivating daily brief. Follow this exact template structure:\n\n"
            "1. GREETING: Start with a warm, time-aware greeting.\n"
            "2. SCHEDULE SUMMARY: Naturally summarize the day's events. If there are no events, provide a cheerful 'clear skies' message.\n"
            "3. VELOCE INSIGHT: Provide one specific piece of productivity advice or a healthy habit tip.\n"
            "4. CLOSING: End with a short, motivating one-liner.\n\n"
            "Keep the tone professional yet friendly and energetic. Remove the GREETING, SCHEDULE SUMMARY, VELOCE INSIGHT, and CLOSING labels from the final output."
        )
        user_prompt = (
            f"Current Time: {now_iso}. Timezone: {timezone}.\n\n"
            f"Today's Schedule:\n{events_str}\n\n"
            "Please generate my daily brief using the Veloce template."
        )

        try:
            started = time.perf_counter()
            self._rate_limiter.acquire()

            log_info(logger, "glm_generate_brief_payload", system_prompt=system_prompt, user_prompt=user_prompt)

            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=2048,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            # Detailed logging to diagnose 'null' responses
            choice = response.choices[0] if response.choices else None
            finish_reason = getattr(choice, "finish_reason", "unknown") if choice else "no_choice"
            raw_content = choice.message.content if choice and choice.message else None

            log_info(
                logger, 
                "glm_generate_brief_response_received", 
                elapsed_ms=elapsed_ms, 
                finish_reason=finish_reason,
                has_content=raw_content is not None,
                raw_response=raw_content
            )

            content = raw_content
            if content:
                content = content.strip()
            else:
                content = f"Good morning! Here are your events for today:\n\n{events_str}"
            
            log_info(logger, "glm_generate_brief_done", elapsed_ms=elapsed_ms, content=content)
            return content
        except Exception as exc:
            log_warning(logger, "glm_generate_brief_failed", error=str(exc))
            return f"Good morning! Here are your events for today:\n\n{events_str}"

glm_service = GlmService()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/extract", response_model=GlmExtraction)
def extract(payload: ExtractRequest):
    return glm_service.extract_tasks(
        inbound=payload.inbound,
        retrieved_context=payload.retrieved_context,
        scheduled_tasks=payload.scheduled_tasks,
        request_id=payload.request_id,
        conflict_context=payload.conflict_context
    )

@app.post("/generate-brief")
def generate_brief(payload: BriefRequest):
    message = glm_service.generate_brief(
        events=payload.events,
        now_iso=payload.now_iso,
        timezone=payload.timezone
    )
    return {"message": message}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
