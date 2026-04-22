import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from veloce.orchestrator.context_service import ContextService
from veloce.orchestrator.db import ContextRow, SQLiteStore
from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.models import (
    ContextIngestRequest,
    ContextRetrieveRequest,
    ContextRetrieveResponse,
    SchedulerInbound,
    SchedulerResponse,
)
from veloce.orchestrator.pipeline import SchedulerPipeline
from veloce.orchestrator.scheduling_engine import GoogleCalendarClient, SchedulingEngine

DEFAULT_TIMEZONE = os.getenv("GENERIC_TIMEZONE", "Asia/Kuala_Lumpur")
DEFAULT_DB = os.getenv("VELOCE_DB_PATH", "data/veloce.db")

store = SQLiteStore(DEFAULT_DB)
context_service = ContextService(store)
glm_client = GlmClient()
calendar_client = GoogleCalendarClient()
scheduling_engine = SchedulingEngine(calendar_client=calendar_client)
pipeline = SchedulerPipeline(glm_client=glm_client, scheduling_engine=scheduling_engine)

app = FastAPI(title="Veloce Orchestrator", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "ok": "true",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/telegram-context-ingest")
def telegram_context_ingest(payload: ContextIngestRequest) -> dict[str, object]:
    inserted = store.ingest_context(
        ContextRow(
            chat_id=payload.chat_id,
            message_id=payload.message_id,
            sender_id=payload.sender_id,
            chat_title=payload.chat_title,
            message=payload.message,
            source=payload.source,
            date=payload.date,
        )
    )

    return {
        "ok": True,
        "inserted": inserted,
        "deduped": not inserted,
    }


@app.post("/telegram-context-retrieve", response_model=ContextRetrieveResponse)
def telegram_context_retrieve(payload: ContextRetrieveRequest) -> ContextRetrieveResponse:
    return context_service.retrieve(
        chat_id=payload.chat_id,
        query=payload.query.strip().lower(),
        limit=payload.limit,
        since=payload.since.isoformat() if payload.since else None,
    )


@app.post("/veloce-task-scheduler", response_model=SchedulerResponse)
def veloce_task_scheduler(payload: SchedulerInbound) -> SchedulerResponse:
    try:
        normalized = pipeline.normalize_inbound(payload, default_timezone=DEFAULT_TIMEZONE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    context_items = []
    if normalized.chat_id is not None:
        retrieved = context_service.retrieve(
            chat_id=normalized.chat_id,
            query=normalized.raw_text.strip().lower(),
            limit=8,
            since=None,
        )
        context_items = retrieved.items

    result = pipeline.run(inbound=normalized, retrieved_context=context_items)
    return result
