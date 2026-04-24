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
    request_id: Optional[str] = None

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
        request_id: Optional[str] = None
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

        user_prompt = user_prompt_template.format(
            now=now,
            timezone=inbound.timezone,
            context_history=context_str,
            raw_text=inbound.raw_text
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

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            log_warning(logger, "glm_request_failed", request_id=request_id, error=str(exc))
            raise HTTPException(status_code=500, detail=f"GLM request failed: {exc}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content = response.choices[0].message.content if response.choices else None

        if not content:
            return GlmExtraction(tasks=[], metadata={"error": "empty_response"})

        parsed = json.loads(content) if isinstance(content, str) else content
        raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        tasks: List[TaskCandidate] = []
        for item in raw_tasks:
            tasks.append(
                TaskCandidate(
                    task_name=item.get("task_name", ""),
                    deadline_iso=item.get("deadline_iso", ""),
                    start_time_iso=item.get("start_time_iso"),
                    estimated_duration_minutes=item.get("estimated_duration_minutes", 90),
                    confidence=item.get("confidence", 0.5),
                    needs_clarification=bool(item.get("needs_clarification", False)),
                    clarification_question=item.get("clarification_question"),
                )
            )

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

glm_service = GlmService()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/extract", response_model=GlmExtraction)
def extract(payload: ExtractRequest):
    return glm_service.extract_tasks(
        inbound=payload.inbound,
        retrieved_context=payload.retrieved_context,
        request_id=payload.request_id
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
