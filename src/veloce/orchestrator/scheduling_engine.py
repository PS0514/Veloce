import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import requests

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import TaskCandidate

logger = get_logger(__name__)


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime
    summary: str


@dataclass(frozen=True)
class ScheduleResult:
    scheduled: bool
    state: str
    reason: str
    calendar_event_id: str | None = None
    calendar_link: str | None = None
    proposed_start: str | None = None
    proposed_end: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None


class GoogleCalendarClient:
    def __init__(self) -> None:
        self.service_url = os.getenv("CALENDAR_SERVICE_URL", "http://localhost:8002").rstrip("/")
        # We still need 'enabled' for some local checks in app.py
        # We can fetch this from the service's health check or just use an env var
        self.enabled = os.getenv("ENABLE_GOOGLE_SYNC", "true").lower() == "true"
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    def create_event(self, *, task: TaskCandidate, start: datetime, end: datetime, timezone_name: str) -> dict:
        url = f"{self.service_url}/create-event"
        payload = {
            "task": task.dict(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone_name": timezone_name
        }
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def quick_add_event(self, *, text: str) -> dict:
        url = f"{self.service_url}/quick-add"
        payload = {"text": text}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_busy_intervals(self, *, time_min: datetime, time_max: datetime) -> List[BusyInterval]:
        url = f"{self.service_url}/busy-intervals"
        params = {
            "time_min": time_min.isoformat(),
            "time_max": time_max.isoformat()
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return [BusyInterval(**item) for item in resp.json()]


class SchedulingEngine:
    def __init__(self, calendar_client: GoogleCalendarClient) -> None:
        self.calendar_client = calendar_client
        self.service_url = calendar_client.service_url

    def schedule(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        request_id: str | None = None,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> ScheduleResult:
        url = f"{self.service_url}/schedule"
        payload = {
            "task": task.dict(),
            "timezone_name": timezone_name,
            "request_id": request_id,
            "ephemeral_busy_slots": [
                {"start": b.start.isoformat(), "end": b.end.isoformat(), "summary": b.summary} 
                for b in (ephemeral_busy_slots or [])
            ]
        }
        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            return ScheduleResult(**data)
        except Exception as exc:
            log_warning(logger, "calendar_client_remote_failed", error=str(exc))
            return ScheduleResult(
                scheduled=False,
                reason=f"Remote scheduling service error: {exc}",
                state="calendar_read_failed"
            )
