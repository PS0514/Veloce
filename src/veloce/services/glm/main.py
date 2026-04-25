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

class StrategizeRequest(BaseModel):
    task: TaskCandidate
    inbound: NormalizedInbound
    workload_context: Optional[List[dict]] = None
    historical_bias: Optional[str] = None
    request_id: Optional[str] = None

class BriefRequest(BaseModel):
    events: List[dict]
    unconfirmed_tasks: Optional[List[dict]] = None
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

    def _clean_json_content(self, content: str) -> str:
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.lstrip("`")
            if clean.startswith("json"):
                clean = clean[4:].strip()
            else:
                clean = clean.strip()
            if clean.endswith("```"):
                clean = clean[:-3].strip()
        return clean

    def strategize_tasks(
        self,
        task: TaskCandidate,
        inbound: NormalizedInbound,
        workload_context: Optional[List[dict]] = None,
        historical_bias: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> List[TaskCandidate]:
        if not self.api_key or self._client is None:
            return [task]

        base_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
        system_prompt_path = base_dir / "glm" / "prompt" / "strategist_system_prompt.txt"

        try:
            system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            log_warning(logger, "missing_strategist_prompt", request_id=request_id)
            return [task]

        workload_str = ""
        if workload_context:
            workload_str = "Current Week Load:\n" + "\n".join([f"- {e['summary']} at {e['start']}" for e in workload_context])

        user_prompt = (
            f"Current Time: {inbound.inbound_date}. Timezone: {inbound.timezone}.\n"
            f"Primary Task: {json.dumps(task.dict())}\n"
            f"{workload_str}\n"
            f"Historical Bias: {historical_bias or 'No historical data yet.'}\n\n"
            "Generate a realistic decomposition and plan."
        )

        log_info(logger, "glm_strategize_start", request_id=request_id, task=task.task_name)

        try:
            self._rate_limiter.acquire(request_id=request_id)
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content if response.choices else None
            if not content:
                log_warning(logger, "glm_strategize_empty", request_id=request_id)
                return [task]

            log_info(logger, "glm_strategize_raw_response", request_id=request_id, content=content)
            
            clean_content = self._clean_json_content(content)
            parsed = json.loads(clean_content)
            raw_tasks = parsed.get("tasks", [])

            final_tasks: List[TaskCandidate] = []
            found_original = False

            for item in raw_tasks:
                tc = TaskCandidate(
                    task_name=item.get("task_name"),
                    deadline_iso=item.get("deadline_iso"),
                    start_time_iso=item.get("start_time_iso"),
                    estimated_duration_minutes=item.get("estimated_duration_minutes", 60),
                    confidence=1.0,
                    needs_clarification=False
                )
                final_tasks.append(tc)
                if tc.task_name == task.task_name:
                    found_original = True

            if not found_original:
                final_tasks.insert(0, task)

            log_info(logger, "glm_strategize_done", request_id=request_id, count=len(final_tasks))
            return final_tasks

        except Exception as e:
            log_warning(logger, "glm_strategize_failed", error=str(e), request_id=request_id)
            return [task]

    def extract_tasks(
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
        try:
            self._rate_limiter.acquire(request_id=request_id)
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            content = response.choices[0].message.content if response.choices else None

            if not content:
                log_warning(logger, "glm_empty_response", request_id=request_id)
                return GlmExtraction(tasks=[], metadata={"error": "empty_response"})

            log_info(logger, "glm_raw_response", request_id=request_id, content=content)

            clean_content = self._clean_json_content(content)
            parsed = json.loads(clean_content)
            raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
            tasks: List[TaskCandidate] = []
            for item in raw_tasks:
                if not isinstance(item, dict): continue
                try:
                    tasks.append(TaskCandidate(
                        task_name=str(item.get("task_name") or "Unnamed Task"),
                        deadline_iso=str(item.get("deadline_iso") or ""),
                        start_time_iso=item.get("start_time_iso"),
                        estimated_duration_minutes=int(item.get("estimated_duration_minutes") or 90),
                        confidence=float(item.get("confidence") if item.get("confidence") is not None else 0.5),
                        needs_clarification=bool(item.get("needs_clarification", False)),
                        clarification_question=item.get("clarification_question"),
                    ))
                except: continue

            log_info(logger, "glm_request_done", request_id=request_id, elapsed_ms=elapsed_ms, tasks_found=len(tasks))
            return GlmExtraction(tasks=tasks, metadata={"elapsed_ms": elapsed_ms})

        except Exception as e:
            log_warning(logger, "glm_extraction_failed", error=str(e), request_id=request_id)
            return self._fallback_extraction(inbound, request_id=request_id)

    def _fallback_extraction(self, inbound: NormalizedInbound, request_id: Optional[str] = None) -> GlmExtraction:
        return GlmExtraction(tasks=[], metadata={"fallback": True})

    def generate_brief(self, events: List[dict], unconfirmed_tasks: Optional[List[dict]] = None, now_iso: str = "", timezone: str = "") -> str:
        if not self.api_key or self._client is None:
            return "Good morning! Hope you have a great day."
        
        events_str = "\n".join([f"- {e.get('summary')} at {e.get('start')}" for e in events])
        
        feedback_str = ""
        if unconfirmed_tasks:
            feedback_str = "\n\nAlso, please ask the user how long these tasks from yesterday actually took:\n"
            feedback_str += "\n".join([f"- {t['task_name']}" for t in unconfirmed_tasks])
            feedback_str += "\nAsk them to reply to this message with the durations (e.g. 'Task took 60m')."

        system_prompt = "You are Veloce, a productivity assistant. Provide a warm daily brief and ask for task feedback if requested."
        user_prompt = f"Current Time: {now_iso}. Today's Schedule:\n{events_str}{feedback_str}\n\nPlease generate my daily brief."

        try:
            self._rate_limiter.acquire()
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
            )
            return response.choices[0].message.content or "Good morning!"
        except Exception as exc:
            log_warning(logger, "glm_generate_brief_failed", error=str(exc))
            return "Good morning!"

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

@app.post("/strategize", response_model=List[TaskCandidate])
def strategize(payload: StrategizeRequest):
    return glm_service.strategize_tasks(
        task=payload.task,
        inbound=payload.inbound,
        workload_context=payload.workload_context,
        historical_bias=payload.historical_bias,
        request_id=payload.request_id
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
