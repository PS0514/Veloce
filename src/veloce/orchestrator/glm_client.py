import os
import requests
from typing import Optional, List

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import ContextItem, GlmExtraction, NormalizedInbound, TaskCandidate

logger = get_logger(__name__)

class GlmClient:
    def __init__(self) -> None:
        self.service_url = os.getenv("GLM_SERVICE_URL") or "http://glm_service:8001"
        self.service_url = self.service_url.rstrip("/")
        self.model = os.getenv("ILMU_MODEL", "ilmu-glm-5.1") # For backward compatibility in logs
        log_info(logger, "glm_client_init_remote", service_url=self.service_url)

    def extract_tasks(
        self, 
        inbound: NormalizedInbound, 
        retrieved_context: Optional[List[ContextItem]] = None, 
        scheduled_tasks: Optional[List[dict]] = None,
        request_id: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> GlmExtraction:
        url = f"{self.service_url}/extract"
        payload = {
            "inbound": inbound.dict(),
            "retrieved_context": [item.dict() for item in retrieved_context] if retrieved_context else None,
            "scheduled_tasks": scheduled_tasks,
            "request_id": request_id,
            "conflict_context": conflict_context
        }
        
        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            return GlmExtraction(**resp.json())
        except Exception as exc:
            log_warning(logger, "glm_client_remote_failed", error=str(exc))
            return GlmExtraction(tasks=[], metadata={"error": str(exc), "remote": True})

    def strategize_tasks(
        self,
        task: TaskCandidate,
        inbound: NormalizedInbound,
        workload_context: Optional[List[dict]] = None,
        historical_bias: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> List[TaskCandidate]:
        url = f"{self.service_url}/strategize"
        payload = {
            "task": task.dict(),
            "inbound": inbound.dict(),
            "workload_context": workload_context,
            "historical_bias": historical_bias,
            "request_id": request_id
        }
        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            # The service now returns a list of tasks (Agent B output)
            raw_tasks = resp.json()
            return [TaskCandidate(**t) for t in raw_tasks]
        except Exception as exc:
            log_warning(logger, "glm_client_strategize_failed", error=str(exc))
            return [task]

    def generate_brief(self, events: List[dict], unconfirmed_tasks: Optional[List[dict]] = None, now_iso: str = "", timezone: str = "") -> str:
        url = f"{self.service_url}/generate-brief"
        payload = {
            "events": events,
            "unconfirmed_tasks": unconfirmed_tasks,
            "now_iso": now_iso,
            "timezone": timezone
        }
        try:
            resp = requests.post(url, json=payload, timeout=300)
            resp.raise_for_status()
            return resp.json().get("message", "")
        except Exception as exc:
            log_warning(logger, "glm_client_generate_brief_failed", error=str(exc))
            return "Good morning! Hope you have a productive day."

class _RateLimiter:
    """Legacy placeholder if needed by other imports."""
    def __init__(self, max_rpm: int = 10) -> None:
        pass
    def acquire(self, request_id: str | None = None) -> float:
        return 0.0
