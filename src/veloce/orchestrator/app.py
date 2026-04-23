import os
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, HTTPException

from veloce.orchestrator.db import ContextRow
from veloce.orchestrator.dependencies import OrchestratorServices, build_services
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import (
    ContextIngestRequest,
    ContextRetrieveRequest,
    ContextRetrieveResponse,
    NormalizedInbound,
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
    
    inbounds = payload if isinstance(payload, list) else [payload]
    if not inbounds:
        return []

    # 1. Group messages by chat_id
    grouped_inbounds = defaultdict(list)
    for item in inbounds:
        chat_id = item.chat_id or "unknown_chat"
        grouped_inbounds[chat_id].append(item)

    all_results: list[SchedulerResponse] = []

    # 2. Process each chat group as a single batch
    for chat_id, group in grouped_inbounds.items():
        # Sort chronologically just in case
        group.sort(key=lambda x: x.date or "")
        
        # Combine the text to feed the AI
        combined_text = "\n".join(
            [f"[{item.date}] User {item.sender_id}: {item.message or item.raw_text}" for item in group]
        )
        
        # Create a "Merged" inbound representing the whole conversation batch
        representative_item = group[-1] # Use the latest item for metadata
        merged_inbound = NormalizedInbound(
            source=representative_item.source or "telegram_userbot",
            message_id=representative_item.message_id,
            sender_id=representative_item.sender_id,
            chat_id=representative_item.chat_id,
            chat_title=representative_item.chat_title,
            inbound_date=representative_item.date or datetime.now(timezone.utc).isoformat(),
            timezone=representative_item.timezone or DEFAULT_TIMEZONE,
            raw_text=combined_text,
        )

        req_id = _request_id(
            source="batch_process", 
            chat_id=representative_item.chat_id, 
            message_id=representative_item.message_id
        )
        
        log_info(
            logger,
            "scheduler_batch_received",
            request_id=req_id,
            chat_id=chat_id,
            batch_size=len(group),
        )

        # Retrieve context once for the whole batch
        context_items = []
        if merged_inbound.chat_id is not None:
            retrieval_started = time.perf_counter()
            retrieved = services.context_service.retrieve(
                chat_id=merged_inbound.chat_id,
                query=combined_text.strip().lower(),
                limit=8,
                since=None,
            )
            context_items = retrieved.items
            retrieval_elapsed_ms = int((time.perf_counter() - retrieval_started) * 1000)
            log_info(
                logger,
                "scheduler_context_retrieved",
                request_id=req_id,
                chat_id=merged_inbound.chat_id,
                items=len(context_items),
                elapsed_ms=retrieval_elapsed_ms,
            )

        # Run the pipeline for the merged block of text
        pipeline_started = time.perf_counter()
        results = services.pipeline.run_multi(
            inbound=merged_inbound,
            retrieved_context=context_items,
            request_id=req_id,
        )
        pipeline_elapsed_ms = int((time.perf_counter() - pipeline_started) * 1000)

        for result in results:
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
            )
            all_results.append(result)

    # If the original input was a single object, return a single object (the first result)
    # if isinstance(payload, SchedulerInbound):
    #     return all_results[0] if all_results else SchedulerResponse(
    #         scheduled=False,
    #         reason="No results generated",
    #         state="no_results"
    #     )

    return all_results


app = _create_app()
