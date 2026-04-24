import os
import requests
from typing import Optional, List

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import ContextItem, GlmExtraction, NormalizedInbound

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
        request_id: Optional[str] = None
    ) -> GlmExtraction:
        url = f"{self.service_url}/extract"
        payload = {
            "inbound": inbound.dict(),
            "retrieved_context": [item.dict() for item in retrieved_context] if retrieved_context else None,
            "request_id": request_id
        }
        
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            return GlmExtraction(**resp.json())
        except Exception as exc:
            log_warning(logger, "glm_client_remote_failed", error=str(exc))
            # Return empty extraction on failure to maintain legacy behavior
            return GlmExtraction(tasks=[], metadata={"error": str(exc), "remote": True})

class _RateLimiter:
    """Legacy placeholder if needed by other imports."""
    def __init__(self, max_rpm: int = 10) -> None:
        pass
    def acquire(self, request_id: str | None = None) -> float:
        return 0.0
