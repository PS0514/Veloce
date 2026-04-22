import json
import os
import time
from datetime import datetime, timezone

import requests

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import GlmExtraction, NormalizedInbound, TaskCandidate

logger = get_logger(__name__)


class GlmClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("ZAI_API_KEY", "").strip()
        self.model = os.getenv("ZAI_MODEL", "glm-4.5").strip() or "glm-4.5"
        self.base_url = os.getenv("ZAI_CHAT_COMPLETIONS_URL", "").strip()

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
                "reason": "ZAI_API_KEY or ZAI_CHAT_COMPLETIONS_URL not configured",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _truncate(value: str, limit: int = 240) -> str:
        trimmed = value.strip().replace("\n", " ")
        if len(trimmed) <= limit:
            return trimmed
        return f"{trimmed[:limit]}..."

    def extract_tasks(self, inbound: NormalizedInbound, request_id: str | None = None) -> GlmExtraction:
        if not self.api_key or not self.base_url:
            return self._fallback_extraction(inbound, request_id=request_id)

        now = datetime.now(timezone.utc).isoformat()
        system_prompt = "\n".join(
            [
                "You are Veloce extraction engine.",
                "Return ONLY valid JSON with this exact shape:",
                '{"tasks":[{"task_name":"string","deadline_iso":"ISO-8601 string","estimated_duration_minutes":90,"confidence":0.0,"needs_clarification":false,"clarification_question":null}]}',
                "Rules:",
                "1) Extract academic/professional tasks and deadlines.",
                '2) If no actionable task, return {"tasks":[]}.',
                "3) deadline_iso must be timezone-aware ISO-8601.",
                "4) estimated_duration_minutes must be integer >= 15.",
                "5) If uncertain, still output best estimate and set needs_clarification=true.",
            ]
        )
        user_prompt = "\n".join(
            [
                f"Current time: {now}",
                f"Timezone: {inbound.timezone}",
                "Input text:",
                inbound.raw_text,
            ]
        )

        payload = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        log_info(
            logger,
            "glm_request_start",
            request_id=request_id,
            model=self.model,
            url=self.base_url,
            text_len=len(inbound.raw_text),
        )
        response = requests.post(self.base_url, headers=headers, json=payload, timeout=25)
        try:
            response.raise_for_status()
        except requests.HTTPError:
            log_warning(
                logger,
                "glm_request_failed",
                request_id=request_id,
                status=response.status_code,
                body=self._truncate(response.text),
            )
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        data = response.json()
        usage = data.get("usage") if isinstance(data, dict) else None
        log_info(
            logger,
            "glm_request_done",
            request_id=request_id,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            usage=usage,
        )

        content = None
        choices = data.get("choices", [])
        if choices and isinstance(choices[0], dict):
            content = choices[0].get("message", {}).get("content")
        if not content:
            content = data.get("output") or data.get("response") or data

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
