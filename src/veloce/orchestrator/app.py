import asyncio
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from veloce.orchestrator.db import ContextRow, AutomatedMessageRow
from veloce.orchestrator.dependencies import OrchestratorServices, build_services
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import (
    ContextIngestRequest,
    AutomatedMessageIngestRequest,
    ContextRetrieveRequest,
    ContextRetrieveResponse,
    ManualCalendarAddRequest,
    ManualCalendarAddResponse,
    NormalizedInbound,
    SchedulerInbound,
    SchedulerResponse,
)

DEFAULT_TIMEZONE = os.getenv("GENERIC_TIMEZONE", "Asia/Kuala_Lumpur")
DEFAULT_DB = os.getenv("VELOCE_DB_PATH", "data/veloce.db")

logger = get_logger(__name__)
services: OrchestratorServices = build_services(DEFAULT_DB)
router = APIRouter()


async def daily_brief_logic() -> dict[str, str]:
    """Core logic to generate the daily brief message."""
    # 1. Get today's events from Calendar Service based on local timezone
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    now_local = datetime.now(tz)
    
    start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day_local = start_of_day_local + timedelta(days=1)
    
    # 2. Get yesterday's UNCONFIRMED tasks for the Feedback Loop
    yesterday_start = start_of_day_local - timedelta(days=1)
    unconfirmed_tasks = []
    if services.store:
        # Fetch tasks from yesterday that aren't completed yet
        with services.store._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_name, calendar_event_id 
                FROM scheduled_tasks 
                WHERE start_time >= ? AND start_time < ? AND is_completed = 0
                LIMIT 5
                """,
                (yesterday_start.isoformat(), start_of_day_local.isoformat())
            ).fetchall()
            unconfirmed_tasks = [dict(r) for r in rows]

    try:
        events = services.calendar_client.list_events(
            time_min=start_of_day_local, 
            time_max=end_of_day_local
        )
    except Exception as exc:
        log_warning(logger, "daily_brief_calendar_failed", error=str(exc))
        events = []

    # 3. Generate tailored brief using GLM
    event_dicts = []
    for e in events:
        event_dicts.append({
            "id": e.id,
            "summary": e.summary,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
        })

    try:
        message = services.glm_client.generate_brief(
            events=event_dicts,
            unconfirmed_tasks=unconfirmed_tasks,
            now_iso=now_local.isoformat(),
            timezone=DEFAULT_TIMEZONE
        )
    except Exception as exc:
        log_warning(logger, "daily_brief_glm_failed", error=str(exc))
        message = "Good morning! I couldn't reach the AI for your brief, but hope you have a great day!"

    return {"message": message}


async def trigger_daily_brief():
    """Generates the daily brief and sends it to Telegram."""
    log_info(logger, "daily_brief_trigger_start")
    try:
        # We reuse the logic from the endpoint
        brief_data = await daily_brief_logic()
        message = brief_data.get("message")
        if message:
            # Inform the request and then send to telegram
            res = await services.telegram_client.send_notification(message)
            if isinstance(res, dict) and res.get("status") == "sent":
                services.store.ingest_automated_message(
                    AutomatedMessageRow(
                        chat_id=res["chat_id"],
                        message_id=res["message_id"],
                        bot_type=res["bot_type"],
                        task_name="Daily Brief"
                    )
                )
            log_info(logger, "daily_brief_trigger_success")
        else:
            log_warning(logger, "daily_brief_trigger_no_message")
    except Exception as exc:
        log_warning(logger, "daily_brief_trigger_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Trigger daily brief on startup (informs the request)
    asyncio.create_task(trigger_daily_brief())
    yield


def _request_id(*, source: str | None, chat_id: str | int | None, message_id: str | int | None) -> str:
    source_part = (source or "unknown").strip() or "unknown"
    chat_part = str(chat_id) if chat_id is not None else "na"
    message_part = str(message_id) if message_id is not None else "na"
    return f"{source_part}:{chat_part}:{message_part}"


from fastapi.middleware.cors import CORSMiddleware


def _create_app() -> FastAPI:
    app = FastAPI(title="Veloce Orchestrator", version="0.1.0", lifespan=lifespan)
    
    # Add CORS middleware for the Chrome extension
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for local dev
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
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


@router.post("/telegram-automated-message-ingest")
def telegram_automated_message_ingest(
    payload: AutomatedMessageIngestRequest | list[AutomatedMessageIngestRequest]
) -> dict[str, object] | list[dict[str, object]]:
    def _process_single(item: AutomatedMessageIngestRequest) -> dict[str, object]:
        log_info(
            logger,
            "automated_message_ingest_received",
            chat_id=item.chat_id,
            message_id=item.message_id,
            bot_type=item.bot_type,
        )
        inserted = services.store.ingest_automated_message(
            AutomatedMessageRow(
                chat_id=item.chat_id,
                message_id=item.message_id,
                bot_type=item.bot_type,
                trigger_msg_id=item.trigger_msg_id,
                task_name=item.task_name,
            )
        )
        return {"ok": True, "inserted": inserted}

    if isinstance(payload, list):
        return [_process_single(item) for item in payload]
    return _process_single(payload)


class FeedbackRequest(BaseModel):
    calendar_event_id: str
    actual_duration_minutes: int

@router.post("/task-feedback")
def task_feedback(payload: FeedbackRequest):
    success = services.store.update_task_feedback(
        payload.calendar_event_id, 
        payload.actual_duration_minutes
    )
    if not success:
        raise HTTPException(status_code=404, detail="Task not found in database.")
    return {"status": "success", "message": "Feedback recorded."}

@router.post("/veloce-task-scheduler", response_model=SchedulerResponse | list[SchedulerResponse])
def veloce_task_scheduler(
    payload: SchedulerInbound | list[SchedulerInbound]
) -> SchedulerResponse | list[SchedulerResponse]:
    
    inbounds = payload if isinstance(payload, list) else [payload]
    if not inbounds:
        return []

    # 1. Group messages by chat_id FIRST
    grouped_inbounds = defaultdict(list)
    for item in inbounds:
        chat_id = item.chat_id or "unknown_chat"
        grouped_inbounds[chat_id].append(item)

    all_results: list[SchedulerResponse] = []

    # 2. Process each chat group
    for chat_id, group in grouped_inbounds.items():
        # Sort chronologically
        group.sort(key=lambda x: x.date or "")
        
        filtered_group = []
        for item in group:
            # STRICT DROP: Never treat automated messages as part of the user's command batch.
            # (Context is safely handled via reply_to_text and trigger DB lookups).
            if item.chat_id and item.message_id:
                if services.store.is_automated_message(item.chat_id, item.message_id):
                    log_info(logger, "scheduler_skipping_automated_message_strict", chat_id=item.chat_id, message_id=item.message_id)
                    continue

            # Fallback for bot messages not in the automated_messages table
            if getattr(item, "is_bot", False):
                log_info(logger, "scheduler_skipping_automated_message_fallback_strict", chat_id=item.chat_id, message_id=item.message_id)
                continue
            
            filtered_group.append(item)

        if not filtered_group:
            continue

        # Combine the text to feed the AI
        combined_text = "\n".join(
            [f"[{item.date}] User {item.sender_id}: {item.message or item.raw_text}" for item in filtered_group]
        )
        
        # Check if any message in the batch was a reply to me
        reply_to_me = any(item.reply_to_me for item in filtered_group)
        reply_to_msg_id = next((item.reply_to_msg_id for item in filtered_group if item.reply_to_me), None)
        reply_to_text = next((item.reply_to_text for item in filtered_group if item.reply_to_me), None)

        # Create a "Merged" inbound representing the whole conversation batch
        representative_item = filtered_group[-1] # Use the latest remaining item for metadata
        merged_inbound = NormalizedInbound(
            source=representative_item.source or "telegram_userbot",
            message_id=representative_item.message_id,
            sender_id=representative_item.sender_id,
            chat_id=representative_item.chat_id,
            chat_title=representative_item.chat_title,
            inbound_date=representative_item.date or datetime.now(timezone.utc).isoformat(),
            timezone=representative_item.timezone or DEFAULT_TIMEZONE,
            raw_text=combined_text,
            reply_to_me=reply_to_me,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_text=reply_to_text,
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

        # 3. Check for Feedback Pattern (e.g., "History Essay took 60m")
        feedback_match = re.search(r"(.+?)\s+took\s+(\d+)\s*(m|min|minutes|h|hours)", combined_text, re.IGNORECASE)
        if feedback_match:
            task_name_query = feedback_match.group(1).strip()
            duration_val = int(feedback_match.group(2))
            unit = feedback_match.group(3).lower()
            
            if unit.startswith("h"):
                duration_val *= 60
            
            # Find the most recent task with this name in the DB
            with services.store._connect() as conn:
                row = conn.execute(
                    "SELECT calendar_event_id FROM scheduled_tasks WHERE task_name LIKE ? ORDER BY inserted_at DESC LIMIT 1",
                    (f"%{task_name_query}%",)
                ).fetchone()
                
                if row:
                    services.store.update_task_feedback(row["calendar_event_id"], duration_val)
                    log_info(logger, "scheduler_feedback_captured", task=task_name_query, duration=duration_val)

        # Retrieve context once for the whole batch
        context_items = []
        if merged_inbound.chat_id is not None:
            retrieval_started = time.perf_counter()
            
            # 1. Semantic Search: Find messages matching the keywords
            retrieved_semantic = services.context_service.retrieve(
                chat_id=merged_inbound.chat_id,
                query=combined_text.strip().lower(),
                limit=5,
                since=None,
            )
            
            # 2. Chronological Search: ALWAYS fetch the last few messages 
            # This guarantees the AI sees the bot's recent questions even if you just say "yes"
            retrieved_recent = services.context_service.retrieve(
                chat_id=merged_inbound.chat_id,
                query="", # Empty query triggers chronological fallback in db.py
                limit=5,
                since=None,
            )
            
            # Combine and deduplicate
            existing_ids = set()
            for item in retrieved_semantic.items + retrieved_recent.items:
                if item.message_id not in existing_ids:
                    context_items.append(item)
                    existing_ids.add(item.message_id)
            
            retrieval_elapsed_ms = int((time.perf_counter() - retrieval_started) * 1000)
            log_info(
                logger,
                "scheduler_context_retrieved",
                request_id=req_id,
                chat_id=merged_inbound.chat_id,
                items=len(context_items),
                elapsed_ms=retrieval_elapsed_ms,
            )

        # 3b. If it's a reply, explicitly fetch recent history to guarantee context
        if merged_inbound.reply_to_me and merged_inbound.chat_id is not None:
            # First, fetch local history (the DM or the same group)
            history_started = time.perf_counter()
            recent_history = services.context_service.retrieve(
                chat_id=merged_inbound.chat_id,
                query="", # Triggers chronological fallback
                limit=15,
                since=None,
            )
            history_elapsed_ms = int((time.perf_counter() - history_started) * 1000)
            
            existing_ids = {item.message_id for item in context_items}
            added_count = 0
            for item in recent_history.items:
                if item.message_id not in existing_ids:
                    context_items.append(item)
                    added_count += 1
            
            log_info(
                logger,
                "scheduler_reply_history_added",
                request_id=req_id,
                chat_id=merged_inbound.chat_id,
                added=added_count,
                elapsed_ms=history_elapsed_ms,
            )

            # SECOND: Extract EXACT original source context using trigger DB tracking
            if merged_inbound.reply_to_msg_id:
                trigger_context = services.context_service.retrieve_trigger_context(
                    chat_id=merged_inbound.chat_id,
                    automated_msg_id=merged_inbound.reply_to_msg_id
                )
                if trigger_context:
                    existing_ids = {item.message_id for item in context_items}
                    added_trigger_count = 0
                    for item in trigger_context:
                        if item.message_id not in existing_ids:
                            context_items.append(item)
                            added_trigger_count += 1
                    log_info(logger, "scheduler_trigger_context_added", request_id=req_id, added=added_trigger_count)
            
            # THIRD: Fallback to [Ref:...] tag if DB lookup failed or for cross-chat replies
            if merged_inbound.reply_to_text:
                # Look for our hidden tracker: [Ref:chat_id:message_id]
                ref_match = re.search(r"\[Ref:(-?\d+):(\d+)\]", merged_inbound.reply_to_text)
                
                if ref_match:
                    source_chat_id = int(ref_match.group(1))
                    source_message_id = int(ref_match.group(2))
                    
                    try:
                        # Retrieve the surrounding context from the original group
                        source_history = services.context_service.retrieve(
                            chat_id=source_chat_id,
                            query="", # Last messages from original group
                            limit=10, 
                            since=None,
                        )
                        
                        added_source_count = 0
                        for item in source_history.items:
                            # We don't check existing_ids because message_ids might overlap across different chats
                            # but the LLM will handle the context block
                            context_items.append(item)
                            added_source_count += 1
                            
                        log_info(
                            logger,
                            "scheduler_source_context_added_via_ref",
                            request_id=req_id,
                            source_chat_id=source_chat_id,
                            target_message_id=source_message_id,
                            added=added_source_count,
                        )
                    except Exception as exc:
                        log_warning(logger, "scheduler_source_ref_lookup_failed", error=str(exc))
                
                # Fallback to Source: title matching if Ref: is missing (for backward compatibility)
                elif "Source: " in merged_inbound.reply_to_text:
                    try:
                        # Extract title from "Source: My Group Name"
                        source_title = merged_inbound.reply_to_text.split("Source: ")[-1].strip().split("\n")[0]
                        source_chat_id = services.store.retrieve_chat_id_by_title(source_title)
                        
                        if source_chat_id and source_chat_id != merged_inbound.chat_id:
                            source_history = services.context_service.retrieve(
                                chat_id=source_chat_id,
                                query="", 
                                limit=10,
                                since=None,
                            )
                            added_source_count = 0
                            for item in source_history.items:
                                context_items.append(item)
                                added_source_count += 1
                            
                            log_info(
                                logger,
                                "scheduler_source_context_added_via_title",
                                request_id=req_id,
                                source_title=source_title,
                                source_chat_id=source_chat_id,
                                added=added_source_count,
                            )
                    except Exception as exc:
                        log_warning(logger, "scheduler_source_title_lookup_failed", error=str(exc))

                # --- NEW FIX: Strip the Ref tag so it doesn't confuse the AI pipeline ---
                merged_inbound.reply_to_text = re.sub(r"\[Ref:(-?\d+):(\d+)\]", "", merged_inbound.reply_to_text).strip()

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


@router.post("/veloce-manual-calendar-add", response_model=ManualCalendarAddResponse)
def veloce_manual_calendar_add(payload: ManualCalendarAddRequest) -> ManualCalendarAddResponse:
    text = (payload.message or payload.raw_text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing message/raw_text in request payload")

    req_id = _request_id(source=payload.source, chat_id=None, message_id=None)
    log_info(
        logger,
        "manual_calendar_add_received",
        request_id=req_id,
        source=payload.source,
        text_len=len(text),
    )

    if not services.calendar_client.enabled:
        return ManualCalendarAddResponse(
            ok=False,
            scheduled=False,
            status="calendar_disabled",
            state="calendar_disabled",
            message="Google sync is disabled",
            ai_used=False,
        )

    # AI processing for time extraction and event name suggestion
    ai_used = False
    try:
        # 1. Package the text into the object the AI expects
        inbound = NormalizedInbound(
            source=payload.source or "manual_selection",
            message_id=None,
            sender_id=None,
            chat_id=None,
            chat_title=None,
            inbound_date=datetime.now(timezone.utc).isoformat(),
            timezone=payload.timezone or DEFAULT_TIMEZONE,
            raw_text=text,
        )

        # 2. Call the correct method
        ai_response = services.glm_client.extract_tasks(inbound)
        
        extracted_time = None
        suggested_name = None

        # 3. Extract the data from the Pydantic models
        if getattr(ai_response, "tasks", None) and len(ai_response.tasks) > 0:
            first_task = ai_response.tasks[0]
            suggested_name = first_task.task_name
            # If start_time is missing, fallback to the deadline
            extracted_time = first_task.start_time_iso or first_task.deadline_iso 

        ai_used = True
    except Exception as exc:
        print(f"\n--- DEBUG: AI FAILED --- \n{exc}\n------------------------\n")
        log_warning(
            logger,
            "ai_processing_failed",
            request_id=req_id,
            error=str(exc),
        )
        extracted_time = None
        suggested_name = None

    # Combine AI suggestions with manual input
    if extracted_time:
        event_text = f"{suggested_name or 'Event'} at {extracted_time}"
    else:
        # If no time is found, use the AI suggested name. 
        # If the AI failed completely, fallback to the raw highlighted text.
        event_text = suggested_name or text

    try:
        event = services.calendar_client.quick_add_event(text=event_text)
    except Exception as exc:
        log_warning(
            logger,
            "manual_calendar_add_failed",
            request_id=req_id,
            error=str(exc),
        )
        return ManualCalendarAddResponse(
            ok=False,
            scheduled=False,
            status="error",
            state="calendar_create_failed",
            message=f"Failed to create calendar event: {exc}",
            ai_used=ai_used,
        )

    start_raw = ""
    if isinstance(event, dict):
        start_payload = event.get("start")
        if isinstance(start_payload, dict):
            start_raw = str(start_payload.get("dateTime") or start_payload.get("date") or "")

    log_info(
        logger,
        "manual_calendar_add_success",
        request_id=req_id,
        event_id=event.get("id") if isinstance(event, dict) else None,
    )

    return ManualCalendarAddResponse(
        ok=True,
        scheduled=True,
        status="scheduled",
        state="manual_direct_scheduled",
        message="Added directly to Google Calendar.",
        ai_used=False,
        calendar_event_id=str(event.get("id")) if isinstance(event, dict) and event.get("id") else None,
        calendar_link=str(event.get("htmlLink")) if isinstance(event, dict) and event.get("htmlLink") else None,
        title=str(event.get("summary")) if isinstance(event, dict) and event.get("summary") else None,
        date=start_raw.split("T")[0] if start_raw else None,
        time=start_raw.split("T")[1] if "T" in start_raw else None,
    )


@router.get("/daily-brief")
async def daily_brief() -> dict[str, str]:
    return await daily_brief_logic()


app = _create_app()
