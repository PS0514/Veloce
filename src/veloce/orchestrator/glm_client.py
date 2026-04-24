import json
import os
from pathlib import Path
import threading
import time
from datetime import datetime, timezone

from openai import OpenAI

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import ContextItem, GlmExtraction, NormalizedInbound, TaskCandidate

logger = get_logger(__name__)


class _RateLimiter:
    """Thread-safe token-bucket rate limiter with a FIFO queue.

    Requests that arrive while the rate limit is active will block in
    order until their turn comes.  The limiter guarantees at most
    ``max_rpm`` requests per rolling 60-second window by enforcing a
    minimum interval of ``60 / max_rpm`` seconds between calls.
    """

    def __init__(self, max_rpm: int = 10) -> None:
        self._min_interval = 60.0 / max(max_rpm, 1)
        self._max_rpm = max(max_rpm, 1)
        # Protects _last_call and the queue ordering
        self._lock = threading.Lock()
        # Serialises callers so they proceed in FIFO order
        self._queue_lock = threading.Lock()
        self._last_call: float = 0.0
        self._pending: int = 0

    @property
    def max_rpm(self) -> int:
        return self._max_rpm

    def acquire(self, request_id: str | None = None) -> float:
        """Block until the caller is allowed to proceed.

        Returns the number of seconds the caller had to wait.
        """
        # Queue lock ensures FIFO ordering among concurrent callers
        with self._queue_lock:
            with self._lock:
                self._pending += 1
                pending = self._pending

            if pending > 1:
                log_info(
                    logger,
                    "rate_limit_queued",
                    request_id=request_id,
                    queue_position=pending,
                )

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

                log_info(
                    logger,
                    "rate_limit_waiting",
                    request_id=request_id,
                    sleep_seconds=round(sleep_for, 3),
                )
                time.sleep(sleep_for)
                waited += sleep_for

            if waited > 0:
                log_info(
                    logger,
                    "rate_limit_resumed",
                    request_id=request_id,
                    waited_seconds=round(waited, 3),
                )
            return waited


class GlmClient:
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
            "glm_client_init",
            model=self.model,
            rate_limit_rpm=self._rate_limiter.max_rpm,
        )

    def _fallback_extraction(self, inbound: NormalizedInbound, request_id: str | None = None) -> GlmExtraction:
        # Conservative fallback if API is not configured: no scheduling action.
        log_warning(
            logger,
            "glm_fallback",
            request_id=request_id,
            reason="missing_config",
            model=self.model,
            timezone=inbound.timezone,
        )
        return GlmExtraction(
            tasks=[],
            metadata={
                "fallback": True,
                "reason": "ILMU_API_KEY not configured",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _truncate(value: str, limit: int = 240) -> str:
        trimmed = value.strip().replace("\n", " ")
        if len(trimmed) <= limit:
            return trimmed
        return f"{trimmed[:limit]}..."

    def extract_tasks(
        self, 
        inbound: NormalizedInbound, 
        retrieved_context: list[ContextItem] | None = None, 
        request_id: str | None = None
    ) -> GlmExtraction:
        if not self.api_key or self._client is None:
            return self._fallback_extraction(inbound, request_id=request_id)

        now = inbound.inbound_date
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        system_prompt_path = base_dir / "glm" / "prompt" /"system_prompt.txt"
        user_prompt_path = base_dir / "glm" / "prompt" / "user_prompt.txt"
        
        try:
            system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
            user_prompt_template = user_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as e:
            log_warning(logger, "missing_prompt_file", error=str(e))
            # Fallback if the text files are accidentally deleted
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        started = time.perf_counter()
        log_info(
            logger,
            "glm_request_start",
            request_id=request_id,
            model=self.model,
            text_len=len(inbound.raw_text),
        )

        # Block until rate limiter allows this request through
        rate_wait = self._rate_limiter.acquire(request_id=request_id)

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            log_warning(
                logger,
                "glm_request_failed",
                request_id=request_id,
                error=str(exc),
            )
            raise

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage = None
        if hasattr(response, "usage") and response.usage is not None:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
        log_info(
            logger,
            "glm_request_done",
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            rate_wait_seconds=round(rate_wait, 3),
            usage=usage,
        )

        # Extract content from the SDK response object
        content = None
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content

        if not content:
            log_warning(
                logger,
                "glm_empty_response",
                request_id=request_id,
            )
            return GlmExtraction(
                tasks=[],
                metadata={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "model": self.model,
                    "error": "empty_response",
                },
            )

        if isinstance(content, dict):
            parsed = content
            log_info(logger, "glm_response_content", request_id=request_id, type="dict")
        else:
            raw = str(content).strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            log_info(
                logger,
                "glm_response_content",
                request_id=request_id,
                type="text",
                preview=self._truncate(raw),
            )
            parsed = json.loads(raw)

        raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        tasks: list[TaskCandidate] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            name = str(item.get("task_name", "")).strip()
            deadline = str(item.get("deadline_iso", "")).strip()
            if not name or not deadline:
                continue
            duration = max(15, int(item.get("estimated_duration_minutes", 90) or 90))
            confidence = float(item.get("confidence", 0.5) or 0.5)
            confidence = min(max(confidence, 0.0), 1.0)
            tasks.append(
                TaskCandidate(
                    task_name=name,
                    deadline_iso=deadline,
                    start_time_iso=item.get("start_time_iso"),
                    estimated_duration_minutes=duration,
                    confidence=confidence,
                    needs_clarification=bool(item.get("needs_clarification", False)),
                    clarification_question=item.get("clarification_question"),
                )
            )

        log_info(logger, "glm_parse_done", request_id=request_id, parsed_tasks=len(tasks))

        return GlmExtraction(
            tasks=tasks,
            metadata={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": self.model,
            },
        )
