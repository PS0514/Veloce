import os
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import TaskCandidate
from veloce.runtime_config import get_config_value, merge_config_values

logger = get_logger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime
    summary: str


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    location: str | None = None


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
    conflicting_intervals: List[BusyInterval] | None = None
    chat_title: str | None = None


def _get_fresh_google_token() -> str:
    """Get a valid Google access token, refreshing if possible."""
    refresh_token = get_config_value("google_refresh_token") or os.getenv("GOOGLE_REFRESH_TOKEN")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not refresh_token or not client_id or not client_secret:
        # Try returning the current access token as a last resort
        return get_config_value("google_access_token") or os.getenv("GOOGLE_ACCESS_TOKEN", "")

    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token", "")).strip()
        if not token:
            return get_config_value("google_access_token") or ""
        
        # Persist the fresh token if we are in a context that can write
        try:
            merge_config_values({"google_access_token": token})
        except Exception:
            pass # Might be read-only filesystem in some docker configs
            
        return token
    except Exception as exc:
        log_warning(logger, "google_token_refresh_failed", error=str(exc))
        return get_config_value("google_access_token") or ""


class GoogleCalendarClient:
    def __init__(self, force_local: bool = False) -> None:
        self.force_local = force_local
        # Prioritize environment variable, then fallback to docker name, then localhost
        self.service_url = os.getenv("CALENDAR_SERVICE_URL") or "http://calendar_service:8002"
        self.service_url = self.service_url.rstrip("/")
        
        # Priority: 1. Runtime Config JSON, 2. Environment Variable
        config_enabled = get_config_value("enable_google_sync")
        if config_enabled is not None:
            if isinstance(config_enabled, str):
                self.enabled = config_enabled.lower() == "true"
            else:
                self.enabled = bool(config_enabled)
        else:
            self.enabled = os.getenv("ENABLE_GOOGLE_SYNC", "true").lower() == "true"

        self.calendar_id = get_config_value("google_calendar_id") or os.getenv("GOOGLE_CALENDAR_ID", "primary")
        
        if self.force_local:
            log_info(logger, "calendar_client_init_local", calendar_id=self.calendar_id)
        else:
            log_info(logger, "calendar_client_init_remote", service_url=self.service_url)

    def create_event(self, *, task: TaskCandidate, start: datetime, end: datetime, timezone_name: str) -> dict:
        if self.force_local:
            return self._create_event_local(task=task, start=start, end=end, timezone_name=timezone_name)
        
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

    def _create_event_local(self, *, task: TaskCandidate, start: datetime, end: datetime, timezone_name: str) -> dict:
        token = _get_fresh_google_token()
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{self.calendar_id}/events"
        
        description = task.study_guide if task.study_guide else f"Scheduled by Veloce AI\n\nOriginal Task: {task.task_name}\nDeadline: {task.deadline_iso}"
        
        payload = {
            "summary": task.task_name,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
            "description": description,
        }
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def quick_add_event(self, *, text: str) -> dict:
        if self.force_local:
            return self._quick_add_event_local(text=text)
            
        url = f"{self.service_url}/quick-add"
        payload = {"text": text}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _quick_add_event_local(self, *, text: str) -> dict:
        token = _get_fresh_google_token()
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{self.calendar_id}/events/quickAdd"
        params = {"text": text}
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_busy_intervals(self, *, time_min: datetime, time_max: datetime) -> List[BusyInterval]:
        if self.force_local:
            return self._list_busy_intervals_local(time_min=time_min, time_max=time_max)
            
        url = f"{self.service_url}/busy-intervals"
        params = {
            "time_min": time_min.isoformat(),
            "time_max": time_max.isoformat()
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return [BusyInterval(**item) for item in resp.json()]

    def _list_busy_intervals_local(self, *, time_min: datetime, time_max: datetime) -> List[BusyInterval]:
        token = _get_fresh_google_token()
        url = f"{GOOGLE_CALENDAR_BASE_URL}/freeBusy"
        payload = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": self.calendar_id}]
        }
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        busy_data = data.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])
        intervals = []
        for b in busy_data:
            intervals.append(BusyInterval(
                start=datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                end=datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
                summary="Busy"
            ))
        return intervals

    def list_events(self, *, time_min: datetime, time_max: datetime) -> List[CalendarEvent]:
        if self.force_local:
            return self._list_events_local(time_min=time_min, time_max=time_max)
            
        url = f"{self.service_url}/list-events"
        params = {
            "time_min": time_min.isoformat(),
            "time_max": time_max.isoformat()
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return [
            CalendarEvent(
                id=item["id"],
                summary=item["summary"],
                start=datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                end=datetime.fromisoformat(item["end"].replace("Z", "+00:00")),
                description=item.get("description"),
                location=item.get("location")
            ) 
            for item in resp.json()
        ]

    def _list_events_local(self, *, time_min: datetime, time_max: datetime) -> List[CalendarEvent]:
        token = _get_fresh_google_token()
        url = f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{self.calendar_id}/events"
        params = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime"
        }
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        items = data.get("items", [])
        events = []
        for item in items:
            start_data = item.get("start", {})
            end_data = item.get("end", {})
            
            # Google can return 'date' for all-day events or 'dateTime'
            start_str = start_data.get("dateTime") or start_data.get("date")
            end_str = end_data.get("dateTime") or end_data.get("date")
            
            if not start_str or not end_str:
                continue

            events.append(CalendarEvent(
                id=item["id"],
                summary=item.get("summary", "No Title"),
                start=datetime.fromisoformat(start_str.replace("Z", "+00:00")),
                end=datetime.fromisoformat(end_str.replace("Z", "+00:00")),
                description=item.get("description"),
                location=item.get("location")
            ))
        return events


class SchedulingEngine:
    def __init__(self, calendar_client: GoogleCalendarClient, force_local: bool = False) -> None:
        self.calendar_client = calendar_client
        self.service_url = calendar_client.service_url
        self.force_local = force_local or calendar_client.force_local

    def check_availability(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> list[BusyInterval]:
        """Check for conflicts without creating an event."""
        if self.force_local:
            return self._check_availability_local(
                task=task,
                timezone_name=timezone_name,
                ephemeral_busy_slots=ephemeral_busy_slots
            )
        
        url = f"{self.service_url}/check-availability"
        payload = {
            "task": task.dict(),
            "timezone_name": timezone_name,
            "ephemeral_busy_slots": [
                {"start": b.start.isoformat(), "end": b.end.isoformat(), "summary": b.summary} 
                for b in (ephemeral_busy_slots or [])
            ]
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return [
                BusyInterval(
                    start=datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                    end=datetime.fromisoformat(item["end"].replace("Z", "+00:00")),
                    summary=item.get("summary", "Busy")
                )
                for item in resp.json()
            ]
        except Exception as exc:
            log_warning(logger, "check_availability_remote_failed", error=str(exc))
            return []

    def _check_availability_local(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> list[BusyInterval]:
        try:
            target_start_str = task.start_time_iso or task.deadline_iso
            if not target_start_str:
                return []
            start_dt = datetime.fromisoformat(target_start_str.replace("Z", "+00:00"))
            duration = timedelta(minutes=task.estimated_duration_minutes)
            end_dt = start_dt + duration

            time_min = start_dt - timedelta(days=1)
            time_max = start_dt + timedelta(days=2)
            busy_intervals = self.calendar_client.list_busy_intervals(time_min=time_min, time_max=time_max)
            
            all_busy = busy_intervals + (ephemeral_busy_slots or [])

            overlaps = []
            for busy in all_busy:
                if start_dt < busy.end and end_dt > busy.start:
                    overlaps.append(busy)
            return overlaps
        except Exception as exc:
            log_warning(logger, "check_availability_local_failed", error=str(exc))
            return []

    def schedule(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        request_id: str | None = None,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> ScheduleResult:
        if self.force_local:
            return self._schedule_local(
                task=task,
                timezone_name=timezone_name,
                request_id=request_id,
                ephemeral_busy_slots=ephemeral_busy_slots
            )

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
            # Increase timeout to 120s
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            
            # Reconstruct BusyInterval objects if they exist in the response
            if "conflicting_intervals" in data and data["conflicting_intervals"]:
                data["conflicting_intervals"] = [
                    BusyInterval(
                        start=datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                        end=datetime.fromisoformat(item["end"].replace("Z", "+00:00")),
                        summary=item.get("summary", "Busy")
                    )
                    for item in data["conflicting_intervals"]
                ]
                
            return ScheduleResult(**data)
        except Exception as exc:
            log_warning(logger, "calendar_client_remote_failed", error=str(exc))
            return ScheduleResult(
                scheduled=False,
                reason=f"Remote scheduling service error: {exc}",
                state="calendar_read_failed"
            )

    def _schedule_local(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        request_id: str | None = None,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> ScheduleResult:
        log_info(logger, "schedule_local_start", task=task.task_name, request_id=request_id, enabled=self.calendar_client.enabled)
        
        if not self.calendar_client.enabled:
            log_warning(logger, "schedule_local_disabled", task=task.task_name, request_id=request_id)
            return ScheduleResult(
                scheduled=False,
                reason="Calendar sync is disabled",
                state="calendar_disabled"
            )

        try:
            # 1. Determine the target start time
            # Fallback to deadline if start_time_iso is missing
            target_start_str = task.start_time_iso or task.deadline_iso
            if not target_start_str:
                return ScheduleResult(
                    scheduled=False,
                    reason="No valid start time or deadline provided",
                    state="insufficient_data"
                )
            start_dt = datetime.fromisoformat(target_start_str.replace("Z", "+00:00"))
            duration = timedelta(minutes=task.estimated_duration_minutes)
            end_dt = start_dt + duration

            # 2. Get busy intervals from Google
            # Look 1 day before and after to be safe
            time_min = start_dt - timedelta(days=1)
            time_max = start_dt + timedelta(days=2)
            busy_intervals = self.calendar_client.list_busy_intervals(time_min=time_min, time_max=time_max)
            
            # Combine with ephemeral slots
            all_busy = busy_intervals + (ephemeral_busy_slots or [])

            # 3. Check for overlap
            overlaps = []
            for busy in all_busy:
                # Standard overlap check: (start1 < end2) and (end1 > start2)
                if start_dt < busy.end and end_dt > busy.start:
                    overlaps.append(busy)

            if overlaps:
                conflicting_event = overlaps[0].summary
                log_info(logger, "schedule_overlap_detected", task=task.task_name, conflict=conflicting_event)
                return ScheduleResult(
                    scheduled=False,
                    reason=f"Conflict detected with '{conflicting_event}'",
                    state="conflict_detected",
                    proposed_start=start_dt.isoformat(),
                    proposed_end=end_dt.isoformat(),
                    needs_clarification=True,
                    clarification_question=f"This overlaps with your '{conflicting_event}'. Should I schedule it anyway or find another time?",
                    conflicting_intervals=overlaps
                )

            # 4. Create the event
            event = self.calendar_client.create_event(
                task=task,
                start=start_dt,
                end=end_dt,
                timezone_name=timezone_name
            )

            return ScheduleResult(
                scheduled=True,
                state="scheduled_success",
                reason="Successfully scheduled in Google Calendar",
                calendar_event_id=event.get("id"),
                calendar_link=event.get("htmlLink"),
                proposed_start=start_dt.isoformat(),
                proposed_end=end_dt.isoformat()
            )

        except Exception as exc:
            import traceback
            log_warning(logger, "schedule_local_failed", error=str(exc), traceback=traceback.format_exc())
            return ScheduleResult(
                scheduled=False,
                reason=f"Local scheduling error: {exc}",
                state="calendar_error"
            )
