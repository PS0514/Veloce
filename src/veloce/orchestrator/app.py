import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, HTTPException

from veloce.orchestrator.db import ContextRow
from veloce.orchestrator.dependencies import OrchestratorServices, build_services
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import (
    ContextIngestRequest,
    ContextRetrieveRequest,
    ContextRetrieveResponse,
    SchedulerInbound,
    SchedulerResponse,
)

DEFAULT_TIMEZONE = os.getenv("GENERIC_TIMEZONE", "Asia/Kuala_Lumpur")
DEFAULT_DB = os.getenv("VELOCE_DB_PATH", "data/veloce.db")

logger = get_logger(__name__)
services: OrchestratorServices = build_services(DEFAULT_DB)
router = APIRouter()


def _request_id(*, source: str | None, chat_id: int | None, message_id: int | None) -> str:
    source_part = (source or "unknown").strip() or "unknown"
    chat_part = str(chat_id) if chat_id is not None else "na"
    message_part = str(message_id) if message_id is not None else "na"
    return f"{source_part}:{chat_part}:{message_part}"


def _create_app() -> FastAPI:
    app = FastAPI(title="Veloce Orchestrator", version="0.1.0")
    app.include_router(router)
    return app

@router.get("/health")
def health() -> dict[str, str]:
    return {
        "ok": "true",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/telegram-context-ingest")
def telegram_context_ingest(
    payload: ContextIngestRequest | list[ContextIngestRequest]
) -> dict[str, object] | list[dict[str, object]]:

    def _process_single(item: ContextIngestRequest) -> dict[str, object]:
        req_id = _request_id(source=item.source, chat_id=item.chat_id, message_id=item.message_id)
        log_info(
            logger,
            "context_ingest_received",
            request_id=req_id,
            source=item.source,
            chat_id=item.chat_id,
            message_id=item.message_id,
        )
        inserted = services.store.ingest_context(
            ContextRow(
                chat_id=item.chat_id,
                message_id=item.message_id,
                sender_id=item.sender_id,
                chat_title=item.chat_title,
                message=item.message,
                source=item.source,
                date=item.date,
            )
        )
        log_info(
            logger,
            "context_ingest_completed",
            request_id=req_id,
            inserted=inserted,
            deduped=not inserted,
        )
        return {"ok": True, "inserted": inserted, "deduped": not inserted}

    # Handle batch payloads (List)
    if isinstance(payload, list):
        return [_process_single(item) for item in payload]

    # Handle single payload
    return _process_single(payload)


@router.post("/telegram-context-retrieve", response_model=ContextRetrieveResponse)
def telegram_context_retrieve(payload: ContextRetrieveRequest) -> ContextRetrieveResponse:
    req_id = _request_id(source="context_retrieve", chat_id=payload.chat_id, message_id=None)
    log_info(
        logger,
        "context_retrieve_received",
        request_id=req_id,
        chat_id=payload.chat_id,
        limit=payload.limit,
        since=payload.since.isoformat() if payload.since else None,
        query_len=len(payload.query.strip()),
    )

    started = time.perf_counter()
    response = services.context_service.retrieve(
        chat_id=payload.chat_id,
        query=payload.query.strip().lower(),
        limit=payload.limit,
        since=payload.since.isoformat() if payload.since else None,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    log_info(
        logger,
        "context_retrieve_completed",
        request_id=req_id,
        returned=response.returned,
        elapsed_ms=elapsed_ms,
    )
    return response


@router.post("/veloce-task-scheduler", response_model=SchedulerResponse | list[SchedulerResponse])
def veloce_task_scheduler(
    payload: SchedulerInbound | list[SchedulerInbound]
) -> SchedulerResponse | list[SchedulerResponse]:

    def _process_single(item: SchedulerInbound) -> SchedulerResponse:
        req_id = _request_id(source=item.source, chat_id=item.chat_id, message_id=item.message_id)
        log_info(
            logger,
            "scheduler_inbound_received",
            request_id=req_id,
            source=item.source,
            chat_id=item.chat_id,
            message_id=item.message_id,
            sender_id=item.sender_id,
            has_message=bool((item.message or item.raw_text or "").strip()),
        )

        try:
            normalized = services.pipeline.normalize_inbound(item, default_timezone=DEFAULT_TIMEZONE)
        except ValueError as exc:
            log_warning(logger, "scheduler_inbound_invalid", request_id=req_id, reason=exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        log_info(
            logger,
            "scheduler_inbound_normalized",
            request_id=req_id,
            timezone=normalized.timezone,
            text_len=len(normalized.raw_text),
        )

        context_items = []
        if normalized.chat_id is not None:
            retrieval_started = time.perf_counter()
            retrieved = services.context_service.retrieve(
                chat_id=normalized.chat_id,
                query=normalized.raw_text.strip().lower(),
                limit=8,
                since=None,
            )
            context_items = retrieved.items
            retrieval_elapsed_ms = int((time.perf_counter() - retrieval_started) * 1000)
            log_info(
                logger,
                "scheduler_context_retrieved",
                request_id=req_id,
                chat_id=normalized.chat_id,
                items=len(context_items),
                elapsed_ms=retrieval_elapsed_ms,
            )
        else:
            log_info(logger, "scheduler_context_skipped", request_id=req_id, reason="no_chat_id")

        pipeline_started = time.perf_counter()
        result = services.pipeline.run(
            inbound=normalized,
            retrieved_context=context_items,
            request_id=req_id,
        )
        pipeline_elapsed_ms = int((time.perf_counter() - pipeline_started) * 1000)

        log_info(
            logger,
            "scheduler_completed",
            request_id=req_id,
            state=result.state,
            scheduled=result.scheduled,
            needs_clarification=result.needs_clarification,
            elapsed_ms=pipeline_elapsed_ms,
            reason=result.reason,
            selected_task=result.selected_task.task_name if result.selected_task else None,
            selected_deadline=result.selected_task.deadline_iso if result.selected_task else None,
            selected_confidence=(
                round(result.selected_task.confidence, 3) if result.selected_task is not None else None
            ),
            calendar_event_id=result.calendar_event_id,
            calendar_link=result.calendar_link,
            clarification_question=result.clarification_question,
        )
        return result

    # Handle batch payloads (List)
    if isinstance(payload, list):
        return [_process_single(item) for item in payload]

    # Handle single payload
    return _process_single(payload)


app = _create_app()
