import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import requests

from veloce.orchestrator.models import TaskCandidate


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
        self.enabled = os.getenv("ENABLE_GOOGLE_SYNC", "false").strip().lower() == "true"
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"
        self.base_url = os.getenv("GOOGLE_CALENDAR_BASE_URL", "https://www.googleapis.com/calendar/v3").rstrip("/")
        self.access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
        self.refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
        self.client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        self.token_url = os.getenv("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token").strip()

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _refresh_access_token(self) -> str:
        if not (self.refresh_token and self.client_id and self.client_secret):
            raise RuntimeError("Google auth is enabled but token settings are incomplete")

        response = requests.post(
            self.token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token", "")).strip()
        if not token:
            raise RuntimeError("Google token refresh did not return access_token")
        return token

    def _auth_headers(self) -> dict[str, str]:
        token = self.access_token
        if not token and self.refresh_token:
            token = self._refresh_access_token()
            self.access_token = token
        if not token:
            raise RuntimeError("GOOGLE_ACCESS_TOKEN or GOOGLE_REFRESH_TOKEN is required when ENABLE_GOOGLE_SYNC=true")

        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _parse_event_time(raw: dict | str | None, default_time: time) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        date_time_value = raw.get("dateTime")
        if date_time_value:
            try:
                return datetime.fromisoformat(str(date_time_value).replace("Z", "+00:00"))
            except ValueError:
                return None

        date_value = raw.get("date")
        if date_value:
            try:
                parsed_date = date.fromisoformat(str(date_value))
                return datetime.combine(parsed_date, default_time, tzinfo=timezone.utc)
            except ValueError:
                return None

        return None

    def list_busy_intervals(self, *, time_min: datetime, time_max: datetime) -> list[BusyInterval]:
        encoded_calendar_id = quote(self.calendar_id, safe="")
        url = f"{self.base_url}/calendars/{encoded_calendar_id}/events"
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": self._iso(time_min),
            "timeMax": self._iso(time_max),
            "maxResults": "2500",
        }
        response = requests.get(url, headers=self._auth_headers(), params=params, timeout=25)
        response.raise_for_status()

        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []

        intervals: list[BusyInterval] = []
        for event in items:
            if not isinstance(event, dict):
                continue
            start = self._parse_event_time(event.get("start"), time.min)
            end = self._parse_event_time(event.get("end"), time.max)
            if start is None or end is None:
                continue
            if end <= start:
                continue
            intervals.append(BusyInterval(start=start, end=end, summary=str(event.get("summary") or "Busy")))

        return intervals

    def create_event(
        self,
        *,
        task: TaskCandidate,
        start: datetime,
        end: datetime,
        timezone_name: str,
    ) -> dict:
        encoded_calendar_id = quote(self.calendar_id, safe="")
        url = f"{self.base_url}/calendars/{encoded_calendar_id}/events"
        payload = {
            "summary": task.task_name,
            "description": "Created by Veloce orchestrator",
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        }
        response = requests.post(url, headers=self._auth_headers(), json=payload, timeout=25)
        response.raise_for_status()
        return response.json()


class SchedulingEngine:
    def __init__(self, calendar_client: GoogleCalendarClient) -> None:
        self.calendar_client = calendar_client

    @staticmethod
    def _merge_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
        if not intervals:
            return []

        ordered = sorted(intervals, key=lambda interval: interval.start)
        merged: list[BusyInterval] = [ordered[0]]

        for current in ordered[1:]:
            last = merged[-1]
            if current.start <= last.end:
                merged[-1] = BusyInterval(
                    start=last.start,
                    end=max(last.end, current.end),
                    summary=last.summary,
                )
            else:
                merged.append(current)

        return merged

    @staticmethod
    def _detect_clash(new_start: datetime, new_end: datetime, existing_events: list[BusyInterval]) -> BusyInterval | None:
        for event in existing_events:
            if new_start < event.end and new_end > event.start:
                return event
        return None

    def _find_free_slot(
        self,
        *,
        now_utc: datetime,
        deadline_utc: datetime,
        duration_minutes: int,
        busy: list[BusyInterval],
    ) -> tuple[datetime, datetime] | None:
        duration = timedelta(minutes=duration_minutes)
        cursor = now_utc

        for slot in self._merge_intervals(busy):
            if slot.end <= cursor:
                continue
            if slot.start > cursor:
                gap = slot.start - cursor
                if gap >= duration:
                    return cursor, cursor + duration
            if slot.end > cursor:
                cursor = slot.end

        if (deadline_utc - cursor) >= duration:
            return cursor, cursor + duration

        return None

    def schedule(self, *, task: TaskCandidate, timezone_name: str) -> ScheduleResult:
        if not self.calendar_client.enabled:
            return ScheduleResult(
                scheduled=False,
                reason="Google sync is disabled",
                state="calendar_disabled",
            )

        now_utc = datetime.now(timezone.utc)
        try:
            deadline_utc = datetime.fromisoformat(task.deadline_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return ScheduleResult(
                scheduled=False,
                reason="Invalid deadline returned by GLM",
                state="invalid_deadline",
                needs_clarification=True,
                clarification_question="I could not parse the deadline. Can you provide an exact date and time?",
            )

        if deadline_utc <= now_utc:
            return ScheduleResult(
                scheduled=False,
                reason="Deadline is in the past",
                state="deadline_in_past",
                needs_clarification=True,
                clarification_question="This deadline appears to be in the past. Should I schedule it anyway?",
            )

        try:
            busy = self.calendar_client.list_busy_intervals(time_min=now_utc, time_max=deadline_utc)
        except Exception as exc:
            return ScheduleResult(
                scheduled=False,
                reason=f"Failed to read calendar availability: {exc}",
                state="calendar_read_failed",
            )

        found = self._find_free_slot(
            now_utc=now_utc,
            deadline_utc=deadline_utc,
            duration_minutes=task.estimated_duration_minutes,
            busy=busy,
        )
        if found is None:
            return ScheduleResult(
                scheduled=False,
                reason="No free slot before deadline",
                state="no_slot",
            )

        proposed_start, proposed_end = found
        conflict = self._detect_clash(proposed_start, proposed_end, busy)
        if conflict is not None:
            return ScheduleResult(
                scheduled=False,
                reason="Proposed slot conflicts with existing event",
                state="needs_clarification",
                needs_clarification=True,
                clarification_question=f"This overlaps with {conflict.summary}. Move it?",
                proposed_start=proposed_start.isoformat(),
                proposed_end=proposed_end.isoformat(),
            )

        try:
            event = self.calendar_client.create_event(
                task=task,
                start=proposed_start,
                end=proposed_end,
                timezone_name=timezone_name,
            )
        except Exception as exc:
            return ScheduleResult(
                scheduled=False,
                reason=f"Failed to create calendar event: {exc}",
                state="calendar_create_failed",
            )

        return ScheduleResult(
            scheduled=True,
            reason="Scheduled successfully",
            state="scheduled",
            calendar_event_id=str(event.get("id")) if event.get("id") else None,
            calendar_link=str(event.get("htmlLink")) if event.get("htmlLink") else None,
            proposed_start=proposed_start.isoformat(),
            proposed_end=proposed_end.isoformat(),
        )
