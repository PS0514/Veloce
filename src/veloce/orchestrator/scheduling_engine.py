import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import requests

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.runtime_config import get_config_value, set_config_value
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
        self.enabled = get_config_value("enable_google_sync", default=False) 
        # Runtime config (veloce_config.json) takes priority, .env is fallback
        self.calendar_id = get_config_value("google_calendar_id") or os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"
        self.base_url = os.getenv("GOOGLE_CALENDAR_BASE_URL", "https://www.googleapis.com/calendar/v3").rstrip("/")
        self.access_token = get_config_value("google_access_token") or os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
        self.refresh_token = get_config_value("google_refresh_token") or os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
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
        # Persist refreshed token to config file so subsequent calls reuse it
        set_config_value("google_access_token", token)
        return token

    def _auth_headers(self) -> dict[str, str]:
        # Always try refresh first if we have a refresh token — stored access
        # tokens expire after ~1 hour and would cause 401 errors.
        if self.refresh_token:
            try:
                token = self._refresh_access_token()
                self.access_token = token
            except Exception:
                # Fall back to stored access token if refresh fails
                token = self.access_token
        else:
            token = self.access_token

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

    def schedule(
        self,
        *,
        task: TaskCandidate,
        timezone_name: str,
        request_id: str | None = None,
        ephemeral_busy_slots: list[BusyInterval] | None = None,
    ) -> ScheduleResult:
        log_info(
            logger,
            "schedule_start",
            request_id=request_id,
            task=task.task_name,
            deadline=task.deadline_iso,
            duration_minutes=task.estimated_duration_minutes,
            timezone=timezone_name,
        )

        if not self.calendar_client.enabled:
            log_info(logger, "schedule_skipped", request_id=request_id, reason="calendar_disabled")
            return ScheduleResult(
                scheduled=False,
                reason="Google sync is disabled",
                state="calendar_disabled",
            )

        now_utc = datetime.now(timezone.utc)
        try:
            deadline_utc = datetime.fromisoformat(task.deadline_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            log_warning(
                logger,
                "schedule_invalid_deadline",
                request_id=request_id,
                deadline=task.deadline_iso,
            )
            return ScheduleResult(
                scheduled=False,
                reason="Invalid deadline returned by GLM",
                state="invalid_deadline",
                needs_clarification=True,
                clarification_question="I could not parse the deadline. Can you provide an exact date and time?",
            )

        if deadline_utc <= now_utc:
            log_info(
                logger,
                "schedule_deadline_past",
                request_id=request_id,
                now=now_utc.isoformat(),
                deadline=deadline_utc.isoformat(),
            )
            return ScheduleResult(
                scheduled=False,
                reason="Deadline is in the past",
                state="deadline_in_past",
                needs_clarification=True,
                clarification_question="This deadline appears to be in the past. Should I schedule it anyway?",
            )

        try:
            busy = self.calendar_client.list_busy_intervals(time_min=now_utc, time_max=deadline_utc)

            if ephemeral_busy_slots:
                busy.extend(ephemeral_busy_slots)

            log_info(logger, "schedule_busy_intervals", request_id=request_id, count=len(busy))
        except Exception as exc:
            logger.exception("schedule_calendar_read_failed request_id=%s error=%s", request_id, exc)
            return ScheduleResult(
                scheduled=False,
                reason=f"Failed to read calendar availability: {exc}",
                state="calendar_read_failed",
            )

        if task.start_time_iso:
            try:
                proposed_start = datetime.fromisoformat(task.start_time_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                proposed_end = proposed_start + timedelta(minutes=task.estimated_duration_minutes)

                if proposed_start <= now_utc:
                    log_info(
                        logger,
                        "schedule_start_time_past",
                        request_id=request_id,
                        now=now_utc.isoformat(),
                        start=proposed_start.isoformat(),
                    )
                    return ScheduleResult(
                        scheduled=False,
                        reason="Requested start time is in the past",
                        state="start_time_in_past",
                        needs_clarification=True,
                        clarification_question="The time you requested is in the past. Should I find the next available slot or skip scheduling?",
                    )

                log_info(
                    logger,
                    "schedule_fixed_time_requested",
                    request_id=request_id,
                    start=proposed_start.isoformat(),
                    end=proposed_end.isoformat(),
                )

                # Check if the requested exact slot clashes with existing events
                conflict = self._detect_clash(proposed_start, proposed_end, busy)
                if conflict:
                    log_info(
                        logger,
                        "schedule_fixed_time_conflict",
                        request_id=request_id,
                        conflict=conflict.summary,
                    )
                    return ScheduleResult(
                        scheduled=False,
                        reason="Exact requested time overlaps with an existing event",
                        state="needs_clarification",
                        needs_clarification=True,
                        clarification_question=f"The time you requested clashes with {conflict.summary}. Shall I find another slot?",
                        proposed_start=proposed_start.isoformat(),
                        proposed_end=proposed_end.isoformat(),
                    )
            except ValueError:
                log_warning(
                    logger,
                    "schedule_invalid_start_time",
                    request_id=request_id,
                    start_time=task.start_time_iso,
                )
                # Fallback if AI gave a bad ISO string
                proposed_start, proposed_end = None, None
        else:
            proposed_start, proposed_end = None, None

        if proposed_start is None:
            # EXISTING LOGIC: Flexible task, find a free slot before the deadline
            found = self._find_free_slot(
                now_utc=now_utc,
                deadline_utc=deadline_utc,
                duration_minutes=task.estimated_duration_minutes,
                busy=busy,
            )
            if found is None:
                log_info(logger, "schedule_no_slot", request_id=request_id)
                return ScheduleResult(
                    scheduled=False,
                    reason="No free slot before deadline",
                    state="no_slot",
                )
            proposed_start, proposed_end = found

        log_info(
            logger,
            "schedule_slot_selected",
            request_id=request_id,
            start=proposed_start.isoformat(),
            end=proposed_end.isoformat(),
        )

        # Final clash detection (redundant for fixed-time but safe, 
        # and necessary for flexible slots)
        conflict = self._detect_clash(proposed_start, proposed_end, busy)
        if conflict is not None:
            log_info(
                logger,
                "schedule_conflict_detected",
                request_id=request_id,
                conflict=conflict.summary,
                conflict_start=conflict.start.isoformat(),
                conflict_end=conflict.end.isoformat(),
            )
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
            logger.exception("schedule_calendar_create_failed request_id=%s error=%s", request_id, exc)
            return ScheduleResult(
                scheduled=False,
                reason=f"Failed to create calendar event: {exc}",
                state="calendar_create_failed",
            )

        log_info(
            logger,
            "schedule_success",
            request_id=request_id,
            event_id=event.get("id"),
            link=event.get("htmlLink"),
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
