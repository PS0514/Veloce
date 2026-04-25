from datetime import datetime, timezone

from veloce.orchestrator.db import SQLiteStore, ScheduledTaskRow
from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.scheduling_engine import BusyInterval, SchedulingEngine
from veloce.orchestrator.logging_utils import get_logger, log_info
from veloce.orchestrator.models import (
    ContextItem,
    NormalizedInbound,
    SchedulerInbound,
    SchedulerResponse,
    TaskCandidate,
)

logger = get_logger(__name__)


class SchedulerPipeline:
    def __init__(
        self,
        glm_client: GlmClient,
        scheduling_engine: SchedulingEngine,
        store: SQLiteStore | None = None,
        min_confidence_for_auto: float = 0.7,
    ) -> None:
        self.glm_client = glm_client
        self.scheduling_engine = scheduling_engine
        self.store = store
        self.min_confidence_for_auto = min_confidence_for_auto

    @staticmethod
    def normalize_inbound(payload: SchedulerInbound, default_timezone: str) -> NormalizedInbound:
        raw_text = (payload.message or payload.raw_text or "").strip()
        if not raw_text:
            raise ValueError("Missing message/raw_text in webhook payload")

        return NormalizedInbound(
            source=(payload.source or "telegram_userbot").strip() or "telegram_userbot",
            message_id=payload.message_id,
            sender_id=payload.sender_id,
            chat_id=payload.chat_id,
            chat_title=payload.chat_title,
            inbound_date=payload.date or datetime.now(timezone.utc).isoformat(),
            timezone=payload.timezone or default_timezone,
            raw_text=raw_text,
            reply_to_me=payload.reply_to_me,
            reply_to_msg_id=payload.reply_to_msg_id,
            reply_to_text=payload.reply_to_text,
        )

    @staticmethod
    def _select_task(tasks: list[TaskCandidate]) -> TaskCandidate | None:
        if not tasks:
            return None

        def key(task: TaskCandidate) -> float:
            try:
                return datetime.fromisoformat(task.deadline_iso.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return float("inf")

        ordered = sorted(tasks, key=key)
        return ordered[0] if ordered else None

    def run(
        self,
        *,
        inbound: NormalizedInbound,
        retrieved_context: list[ContextItem] | None = None,
        request_id: str | None = None,
    ) -> SchedulerResponse:
        """Main entry point for single-task legacy flows. 
        Will return the result of the first task if multiple are found.
        """
        results = self.run_multi(
            inbound=inbound,
            retrieved_context=retrieved_context,
            request_id=request_id
        )
        if not results:
            return SchedulerResponse(
                scheduled=False,
                reason="No actionable task extracted",
                selected_task=None,
                state="decision_no_action",
            )
        return results[0]

    def run_multi(
        self,
        *,
        inbound: NormalizedInbound,
        retrieved_context: list[ContextItem] | None = None,
        request_id: str | None = None,
    ) -> list[SchedulerResponse]:
        log_info(
            logger,
            "pipeline_glm_extract_start",
            request_id=request_id,
            source=inbound.source,
            chat_id=inbound.chat_id,
        )

        # Retrieve already scheduled tasks from DB for AI context
        scheduled_tasks = []
        user_memories = []
        if self.store:
            scheduled_tasks = [dict(row) for row in self.store.retrieve_scheduled_tasks(limit=15)]
            if inbound.chat_id:
                user_memories = [dict(row) for row in self.store.retrieve_memories(inbound.chat_id)]

        extraction = self.glm_client.extract_tasks(
            inbound, 
            retrieved_context=retrieved_context, 
            scheduled_tasks=scheduled_tasks,
            user_memories=user_memories,
            request_id=request_id
        )
        log_info(
            logger,
            "pipeline_glm_extract_done",
            request_id=request_id,
            tasks=len(extraction.tasks),
            memories=len(extraction.extracted_memories),
            intent=extraction.intent,
            metadata=extraction.metadata,
        )

        # Ingest extracted memories if any
        if self.store and inbound.chat_id and extraction.extracted_memories:
            for mem in extraction.extracted_memories:
                self.store.ingest_memory(inbound.chat_id, mem.preference, mem.category)

        # ROUTING
        # ROUTE 1: General Chat
        if extraction.intent == "general_chat":
            return [SchedulerResponse(
                scheduled=False,
                state="conversational_reply",
                reason=extraction.bot_response or "How can I help you?",
                source_chat_id=inbound.chat_id,
                source_message_id=inbound.message_id,
            )]

        # ROUTE 2: Calendar Query ("What do I have tomorrow?")
        elif extraction.intent == "query_calendar":
            return [self._handle_calendar_query(inbound, extraction.query_date_range)]

        # ROUTE 3: Save Memory
        elif extraction.intent == "save_memory":
            pref_list = [m.preference for m in extraction.extracted_memories]
            msg = "I've remembered your preferences."
            if pref_list:
                msg = f"I've remembered: {', '.join(pref_list)}"
            return [SchedulerResponse(
                scheduled=False,
                state="conversational_reply",
                reason=extraction.bot_response or msg,
                source_chat_id=inbound.chat_id,
                source_message_id=inbound.message_id,
            )]

        if not extraction.tasks:
            return [SchedulerResponse(
                scheduled=False,
                reason="No actionable task extracted",
                selected_task=None,
                state="decision_no_action",
            )]

        results: list[SchedulerResponse] = []
        ephemeral_busy_slots: list[BusyInterval] = []

        for task in extraction.tasks:
            log_info(
                logger,
                "pipeline_task_processing",
                request_id=request_id,
                task=task.task_name,
                deadline=task.deadline_iso,
                confidence=round(task.confidence, 3),
                needs_clarification=task.needs_clarification,
            )

            if task.needs_clarification or task.confidence < self.min_confidence_for_auto:
                # Check availability anyway to provide a better question
                overlaps = self.scheduling_engine.check_availability(
                    task=task,
                    timezone_name=inbound.timezone,
                    ephemeral_busy_slots=ephemeral_busy_slots
                )
                
                status_msg = "You are free!" if not overlaps else f"You have a conflict with '{overlaps[0].summary}'."
                question = task.clarification_question or f"I found this task. {status_msg} Should I schedule it?"
                
                logger.info(
                    "pipeline_needs_clarification request_id=%s task=%s reason=confidence_or_flag confidence=%.2f min=%.2f overlaps=%d",
                    request_id,
                    task.task_name,
                    task.confidence,
                    self.min_confidence_for_auto,
                    len(overlaps)
                )
                results.append(SchedulerResponse(
                    scheduled=False,
                    selected_task=task,
                    needs_clarification=True,
                    clarification_question=question,
                    reason=f"Needs clarification. {status_msg}",
                    state="needs_clarification",
                    chat_title=inbound.chat_title,
                    source_chat_id=inbound.chat_id,
                    source_message_id=inbound.message_id,
                ))
                continue

            if retrieved_context is not None and len(retrieved_context) == 0:
                log_info(
                    logger,
                    "pipeline_needs_context",
                    request_id=request_id,
                    task=task.task_name,
                    reason="empty_retrieved_context",
                )
                results.append(SchedulerResponse(
                    scheduled=False,
                    selected_task=task,
                    needs_clarification=True,
                    clarification_question="I could not find enough context. Can you confirm the exact deadline and duration?",
                    reason="More context needed",
                    state="decision_needs_context",
                    chat_title=inbound.chat_title,
                    source_chat_id=inbound.chat_id,
                    source_message_id=inbound.message_id,
                ))
                continue

            schedule_result = self.scheduling_engine.schedule(
                task=task,
                timezone_name=inbound.timezone,
                request_id=request_id,
                ephemeral_busy_slots=ephemeral_busy_slots
            )

            # OPTIMIZATION: If conflict detected, let GLM help resolve it
            if schedule_result.state == "conflict_detected" and schedule_result.conflicting_intervals:
                log_info(logger, "pipeline_conflict_resolution_attempt", request_id=request_id, task=task.task_name)
                
                # Format conflict context for GLM
                conflict_desc = []
                for idx, busy in enumerate(schedule_result.conflicting_intervals):
                    # Robustly handle both objects and dicts
                    if hasattr(busy, "start"):
                        b_start = busy.start
                        b_end = busy.end
                        b_summary = busy.summary
                    else:
                        # Fallback for dicts if reconstruction failed or skipped
                        b_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
                        b_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
                        b_summary = busy.get("summary", "Busy")
                        
                    busy_start_str = b_start.strftime("%H:%M")
                    busy_end_str = b_end.strftime("%H:%M")
                    conflict_desc.append(f"{idx+1}. '{b_summary}' from {busy_start_str} to {busy_end_str}")
                
                conflict_context = "CRITICAL: The following schedule conflicts were detected:\n" + "\n".join(conflict_desc)
                conflict_context += "\n\nPlease determine if the task should be rescheduled to a different time or if I should ask the user for a preference."

                # Call GLM again with the conflict information
                resolution_extraction = self.glm_client.extract_tasks(
                    inbound=inbound,
                    retrieved_context=retrieved_context,
                    request_id=request_id,
                    conflict_context=conflict_context
                )

                if resolution_extraction.tasks:
                    # Use the first suggestion from GLM
                    suggested_task = resolution_extraction.tasks[0]
                    log_info(
                        logger, 
                        "pipeline_conflict_resolution_suggestion", 
                        request_id=request_id, 
                        task=task.task_name,
                        new_start=suggested_task.start_time_iso,
                        needs_clarification=suggested_task.needs_clarification
                    )
                    
                    # Update the task with GLM's suggestion
                    # If GLM suggested a different time, we can either return it for confirmation or try one more time.
                    # For now, let's return it as a clarification/suggestion to the user.
                    results.append(SchedulerResponse(
                        scheduled=False,
                        selected_task=suggested_task,
                        reason=f"Conflict detected. AI suggests: {suggested_task.clarification_question or 'Rescheduling'}",
                        state="conflict_resolved_suggestion",
                        needs_clarification=True,
                        clarification_question=suggested_task.clarification_question or f"I found a conflict with {schedule_result.conflicting_intervals[0].summary}. Should I move it to {suggested_task.start_time_iso}?",
                        chat_title=inbound.chat_title,
                        source_chat_id=inbound.chat_id,
                        source_message_id=inbound.message_id,
                    ))
                    continue

            log_info(
                logger,
                "pipeline_schedule_done",
                request_id=request_id,
                task=task.task_name,
                state=schedule_result.state,
                scheduled=schedule_result.scheduled,
                reason=schedule_result.reason,
            )

            if schedule_result.scheduled and schedule_result.proposed_start and schedule_result.proposed_end:
                try:
                    start_dt = datetime.fromisoformat(schedule_result.proposed_start.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(schedule_result.proposed_end.replace("Z", "+00:00"))
                    ephemeral_busy_slots.append(
                        BusyInterval(
                            start=start_dt,
                            end=end_dt,
                            summary=f"Ephemeral: {task.task_name}"
                        )
                    )
                except ValueError:
                    logger.warning("Failed to parse proposed times for ephemeral memory")

                # Save to persistent DB storage
                if self.store:
                    self.store.ingest_scheduled_task(
                        ScheduledTaskRow(
                            task_name=task.task_name,
                            start_time=schedule_result.proposed_start,
                            end_time=schedule_result.proposed_end,
                            calendar_event_id=schedule_result.calendar_event_id,
                            chat_id=inbound.chat_id,
                            message_id=inbound.message_id,
                        )
                    )

            # Append breakdown to reason if available
            notification_reason = schedule_result.reason
            if schedule_result.scheduled and task.description:
                notification_reason += f"\n\nHere is your task breakdown based on recent context:\n{task.description}"

            results.append(SchedulerResponse(
                scheduled=schedule_result.scheduled,
                selected_task=task,
                reason=notification_reason,
                state=schedule_result.state,
                calendar_event_id=schedule_result.calendar_event_id,
                calendar_link=schedule_result.calendar_link,
                needs_clarification=schedule_result.needs_clarification,
                clarification_question=schedule_result.clarification_question,
                chat_title=inbound.chat_title,
                source_chat_id=inbound.chat_id,
                source_message_id=inbound.message_id,
            ))
        
        return results

    def _handle_calendar_query(self, inbound: NormalizedInbound, date_range: dict | None) -> SchedulerResponse:
        """Fetch events for a date range and generate a conversational brief."""
        if not date_range or "start" not in date_range or "end" not in date_range:
             return SchedulerResponse(
                scheduled=False,
                state="conversational_reply",
                reason="I couldn't determine the date range for your query. Could you be more specific?",
                source_chat_id=inbound.chat_id,
                source_message_id=inbound.message_id,
            )

        try:
            time_min = datetime.fromisoformat(date_range["start"].replace("Z", "+00:00"))
            time_max = datetime.fromisoformat(date_range["end"].replace("Z", "+00:00"))
        except ValueError:
            return SchedulerResponse(
                scheduled=False,
                state="conversational_reply",
                reason="The date range provided by the AI was invalid.",
                source_chat_id=inbound.chat_id,
                source_message_id=inbound.message_id,
            )

        # 1. Fetch events using calendar_client
        events = self.scheduling_engine.calendar_client.list_events(
            time_min=time_min, 
            time_max=time_max
        )
        
        # 2. If no events, return a simple string
        if not events:
             return SchedulerResponse(
                 scheduled=False,
                 state="conversational_reply", 
                 reason="You have no events scheduled for that time.",
                 source_chat_id=inbound.chat_id,
                 source_message_id=inbound.message_id,
            )

        # 3. Format the events into a conversational reply
        # Convert CalendarEvent objects to dicts for glm_client
        event_dicts = [
            {
                "id": e.id,
                "summary": e.summary,
                "start": e.start.isoformat(),
                "end": e.end.isoformat(),
                "description": e.description,
                "location": e.location
            }
            for e in events
        ]
        reply_text = self.glm_client.generate_brief(event_dicts, inbound.inbound_date, inbound.timezone)
        
        return SchedulerResponse(
            scheduled=False,
            state="conversational_reply",
            reason=reply_text,
            source_chat_id=inbound.chat_id,
            source_message_id=inbound.message_id,
        )
