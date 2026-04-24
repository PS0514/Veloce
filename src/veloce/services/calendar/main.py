import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import TaskCandidate
from veloce.orchestrator.scheduling_engine import (
    GoogleCalendarClient, 
    SchedulingEngine, 
    ScheduleResult,
    BusyInterval
)

logger = get_logger(__name__)

app = FastAPI(title="Veloce Calendar Service", version="0.1.0")

class ScheduleRequest(BaseModel):
    task: TaskCandidate
    timezone_name: str
    request_id: Optional[str] = None
    ephemeral_busy_slots: Optional[List[BusyInterval]] = None

class CreateEventRequest(BaseModel):
    task: TaskCandidate
    start: datetime
    end: datetime
    timezone_name: str

class QuickAddRequest(BaseModel):
    text: str

# Instantiate the engine
calendar_client = GoogleCalendarClient()
scheduling_engine = SchedulingEngine(calendar_client)

@app.get("/health")
def health():
    return {"status": "ok", "calendar_enabled": calendar_client.enabled}

@app.post("/schedule", response_model=ScheduleResult)
def schedule(payload: ScheduleRequest):
    log_info(logger, "calendar_service_schedule_start", task=payload.task.task_name, request_id=payload.request_id)
    try:
        result = scheduling_engine.schedule(
            task=payload.task,
            timezone_name=payload.timezone_name,
            request_id=payload.request_id,
            ephemeral_busy_slots=payload.ephemeral_busy_slots
        )
        log_info(logger, "calendar_service_schedule_done", scheduled=result.scheduled, request_id=payload.request_id)
        return result
    except Exception as exc:
        import traceback
        log_warning(logger, "calendar_service_schedule_failed", error=str(exc), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/create-event")
def create_event(payload: CreateEventRequest):
    try:
        event = calendar_client.create_event(
            task=payload.task,
            start=payload.start,
            end=payload.end,
            timezone_name=payload.timezone_name
        )
        return event
    except Exception as exc:
        log_warning(logger, "calendar_service_create_event_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/quick-add")
def quick_add(payload: QuickAddRequest):
    try:
        event = calendar_client.quick_add_event(text=payload.text)
        return event
    except Exception as exc:
        log_warning(logger, "calendar_service_quick_add_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/busy-intervals", response_model=List[BusyInterval])
def list_busy_intervals(time_min: datetime, time_max: datetime):
    try:
        intervals = calendar_client.list_busy_intervals(time_min=time_min, time_max=time_max)
        return intervals
    except Exception as exc:
        log_warning(logger, "calendar_service_busy_intervals_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
