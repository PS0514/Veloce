from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SchedulerInbound(BaseModel):
    source: str = "telegram_userbot"
    message_id: int | None = None
    sender_id: int | None = None
    chat_id: int | None = None
    chat_title: str | None = None
    message: str | None = None
    raw_text: str | None = None
    date: str | None = None
    timezone: str | None = None


class NormalizedInbound(BaseModel):
    source: str
    message_id: int | None
    sender_id: int | None
    chat_id: int | None
    chat_title: str | None
    inbound_date: str
    timezone: str
    raw_text: str


class TaskCandidate(BaseModel):
    task_name: str
    deadline_iso: str
    estimated_duration_minutes: int = Field(ge=15)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None


class GlmExtraction(BaseModel):
    tasks: list[TaskCandidate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextRetrieveRequest(BaseModel):
    chat_id: int
    query: str = ""
    limit: int = Field(default=8, ge=1, le=50)
    since: datetime | None = None


class ContextItem(BaseModel):
    message_id: int
    sender_id: int | None = None
    chat_title: str | None = None
    message: str
    date: str | None = None
    source: str
    score: float


class ContextRetrieveResponse(BaseModel):
    chat_id: int
    query: str
    returned: int
    items: list[ContextItem]


class ContextIngestRequest(BaseModel):
    source: str = "telegram_userbot"
    message_id: int
    sender_id: int | None = None
    chat_id: int
    chat_title: str | None = None
    message: str
    date: str | None = None


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
