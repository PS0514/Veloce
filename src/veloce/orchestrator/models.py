from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SchedulerInbound(BaseModel):
    source: str = "telegram_userbot"
    message_id: str | int | None = None
    sender_id: str | int | None = None
    chat_id: str | int | None = None
    chat_title: str | None = None
    message: str | None = None
    raw_text: str | None = None
    date: str | None = None
    timezone: str | None = None
    reply_to_me: bool = False
    reply_to_msg_id: str | int | None = None
    reply_to_text: str | None = None


class NormalizedInbound(BaseModel):
    source: str
    message_id: str | int | None
    sender_id: str | int | None
    chat_id: str | int | None
    chat_title: str | None
    inbound_date: str
    timezone: str
    raw_text: str
    reply_to_me: bool = False
    reply_to_msg_id: str | int | None
    reply_to_text: str | None


class TaskCandidate(BaseModel):
    task_name: str = "Unnamed Task"
    deadline_iso: str = ""
    start_time_iso: str | None = None  # NEW: For fixed-time events
    estimated_duration_minutes: int = Field(default=90, ge=15)
    confidence: float = Field(default=0.5, ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None


class GlmExtraction(BaseModel):
    tasks: list[TaskCandidate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextRetrieveRequest(BaseModel):
    chat_id: str | int
    query: str = ""
    limit: int = Field(default=8, ge=1, le=50)
    since: datetime | None = None


class ContextItem(BaseModel):
    message_id: str | int
    sender_id: str | int | None = None
    chat_title: str | None = None
    message: str
    date: str | None = None
    source: str
    score: float


class ContextRetrieveResponse(BaseModel):
    chat_id: str | int
    query: str
    returned: int
    items: list[ContextItem]


class ContextIngestRequest(BaseModel):
    source: str = "telegram_userbot"
    message_id: str | int
    sender_id: str | int | None = None
    chat_id: str | int
    chat_title: str | None = None
    message: str
    date: str | None = None


class AutomatedMessageIngestRequest(BaseModel):
    chat_id: str | int
    message_id: str | int
    bot_type: str  # 'userbot' or 'fatherbot'
    trigger_msg_id: str | int | None = None
    task_name: str | None = None


class SchedulerResponse(BaseModel):
    ok: bool = True
    scheduled: bool
    reason: str | None = None
    selected_task: TaskCandidate | None = None
    calendar_event_id: str | None = None
    calendar_link: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    state: str
    chat_title: str | None = None
    source_chat_id: str | int | None = None
    source_message_id: str | int | None = None


class ManualCalendarAddRequest(BaseModel):
    source: str = "manual_selection"
    message: str | None = None
    raw_text: str | None = None
    date: str | None = None
    timezone: str | None = None


class ManualCalendarAddResponse(BaseModel):
    ok: bool = True
    scheduled: bool
    status: str
    message: str
    state: str
    ai_used: bool = False
    calendar_event_id: str | None = None
    calendar_link: str | None = None
    title: str | None = None
    date: str | None = None
    time: str | None = None
