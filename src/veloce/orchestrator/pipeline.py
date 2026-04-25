from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from veloce.orchestrator.db import SQLiteStore, ScheduledTaskRow, MemoryRow
from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.scheduling_engine import BusyInterval, SchedulingEngine
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.orchestrator.models import (
    ContextItem,
    NormalizedInbound,
    SchedulerInbound,
    SchedulerResponse,
    TaskCandidate,
    UserIntent,
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
            "pipeline_intent_classification_start",
            request_id=request_id,
            source=inbound.source,
            chat_id=inbound.chat_id,
        )

        intent_result = self.glm_client.classify_intent(inbound)
        log_info(
            logger,
            "pipeline_intent_classification_done",
            request_id=request_id,
            intent=intent_result.intent,
            confidence=round(intent_result.confidence, 3),
        )

        if intent_result.intent == UserIntent.SCHEDULE_TASK:
            return self._handle_scheduling(inbound, retrieved_context, request_id)
        elif intent_result.intent == UserIntent.QUERY_CALENDAR:
            return self._handle_calendar_query(inbound, intent_result.extracted_entities, request_id)
        elif intent_result.intent == UserIntent.SAVE_MEMORY:
            return self._handle_save_memory(inbound, intent_result.extracted_entities, request_id)
        elif intent_result.intent == UserIntent.GENERAL_CHAT:
            if not inbound.is_direct_interaction:
                log_info(logger, "pipeline_ignored_group_chat", chat_id=inbound.chat_id)
                return [SchedulerResponse(
                    scheduled=False,
                    state="ignored_group_chat",
                    reason="Silently ignored general conversation in an unrelated group.",
                    chat_title=inbound.chat_title,
                    source_chat_id=inbound.chat_id,
                    source_message_id=inbound.message_id,
                )]
            else:
                return self._handle_general_chat(inbound, retrieved_context, request_id)
        else:
            return self._handle_general_chat(inbound, retrieved_context, request_id)

    def _handle_scheduling(
        self,
        inbound: NormalizedInbound,
        retrieved_context: list[ContextItem] | None = None,
        request_id: str | None = None,
    ) -> list[SchedulerResponse]:
        log_info(
            logger,
            "pipeline_scheduling_start",
            request_id=request_id,
            chat_id=inbound.chat_id,
        )

        # Retrieve already scheduled tasks from DB for AI context
        scheduled_tasks = []
        if self.store:
            scheduled_tasks = [dict(row) for row in self.store.retrieve_scheduled_tasks(limit=15)]

        extraction = self.glm_client.extract_tasks(
            inbound, 
            retrieved_context=retrieved_context, 
            scheduled_tasks=scheduled_tasks,
            request_id=request_id
        )
        log_info(
            logger,
            "pipeline_glm_extract_done",
            request_id=request_id,
            tasks=len(extraction.tasks),
            metadata=extraction.metadata,
        )

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
                overlaps = self.scheduling_engine.check_availability(
                    task=task,
                    timezone_name=inbound.timezone,
                    ephemeral_busy_slots=ephemeral_busy_slots
                )
                
                status_msg = "You are free!" if not overlaps else f"You have a conflict with '{overlaps[0].summary}'."
                question = task.clarification_question or f"I found this task. {status_msg} Should I schedule it?"
                
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

            # NEW: STRATEGIST AGENT (Multi-Agent Step)
            log_info(logger, "pipeline_strategizing_start", request_id=request_id, task=task.task_name)
            
            # 1. Fetch Energy Settings from Runtime Config
            from veloce.runtime_config import get_config_value
            energy_settings = {
                "deep_work": f"{get_config_value('deep_work_start', '09:00')} - {get_config_value('deep_work_end', '13:00')}",
                "shallow_work": f"{get_config_value('shallow_work_start', '14:00')} - {get_config_value('shallow_work_end', '17:00')}"
            }
            
            # 2. Fetch Workload Context (next 4 days for better performance)
            workload_context = []
            try:
                tz = ZoneInfo(inbound.timezone)
                now_local = datetime.now(tz)
                end_of_period = now_local + timedelta(days=4)
                
                raw_events = self.scheduling_engine.calendar_client.list_events(
                    time_min=now_local,
                    time_max=end_of_period
                )
                workload_context = [
                    {"summary": e.summary, "start": e.start.isoformat(), "end": e.end.isoformat()}
                    for e in raw_events[:15]
                ]
            except Exception as e:
                log_warning(logger, "pipeline_workload_fetch_failed", error=str(e))

            # 3. Calculate REAL Historical Bias from DB
            historical_bias = "No historical data yet."
            if self.store:
                historical_bias = self.store.calculate_historical_bias()
                
                # Step 2 from Plan: Inject Memories into Scheduling
                user_memories = self.store.retrieve_memories_by_chat(inbound.chat_id)
                if user_memories:
                    memory_context = "\n".join([m["memory_text"] for m in user_memories])
                    historical_bias += f"\nUser Preferences: {memory_context}"
            
            # Update the prompt context with Energy Settings
            historical_bias += f"\nUser Energy Windows: {energy_settings}"

            strategized_tasks = self.glm_client.strategize_tasks(
                task=task,
                inbound=inbound,
                workload_context=workload_context,
                historical_bias=historical_bias,
                request_id=request_id
            )
            log_info(logger, "pipeline_strategizing_done", request_id=request_id, task_count=len(strategized_tasks))

            for subtask in strategized_tasks:
                schedule_result = self.scheduling_engine.schedule(
                    task=subtask,
                    timezone_name=inbound.timezone,
                    request_id=request_id,
                    ephemeral_busy_slots=ephemeral_busy_slots
                )

                if schedule_result.scheduled and schedule_result.proposed_start and schedule_result.proposed_end:
                    try:
                        start_dt = datetime.fromisoformat(schedule_result.proposed_start.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(schedule_result.proposed_end.replace("Z", "+00:00"))
                        ephemeral_busy_slots.append(
                            BusyInterval(
                                start=start_dt,
                                end=end_dt,
                                summary=f"Ephemeral: {subtask.task_name}"
                            )
                        )
                    except ValueError:
                        logger.warning("Failed to parse proposed times for ephemeral memory")

                    if self.store:
                        self.store.ingest_scheduled_task(
                            ScheduledTaskRow(
                                task_name=subtask.task_name,
                                start_time=schedule_result.proposed_start,
                                end_time=schedule_result.proposed_end,
                                calendar_event_id=schedule_result.calendar_event_id,
                                chat_id=inbound.chat_id,
                                message_id=inbound.message_id,
                            )
                        )

                if subtask.task_name == task.task_name:
                    results.append(SchedulerResponse(
                        scheduled=schedule_result.scheduled,
                        selected_task=subtask,
                        reason=schedule_result.reason if len(strategized_tasks) == 1 else f"{schedule_result.reason} (plus {len(strategized_tasks)-1} support tasks)",
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

    def _handle_calendar_query(
        self,
        inbound: NormalizedInbound,
        entities: dict,
        request_id: str | None = None,
    ) -> list[SchedulerResponse]:
        log_info(logger, "pipeline_calendar_query_start", request_id=request_id, entities=entities)
        
        start_str = entities.get("start_time")
        end_str = entities.get("end_time")
        
        try:
            time_min = datetime.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else datetime.now(timezone.utc)
            time_max = datetime.fromisoformat(end_str.replace("Z", "+00:00")) if end_str else time_min + timedelta(days=1)
        except ValueError:
            time_min = datetime.now(timezone.utc)
            time_max = time_min + timedelta(days=1)
        
        events = self.scheduling_engine.calendar_client.list_events(
            time_min=time_min, 
            time_max=time_max
        )
        
        event_dicts = [{"summary": e.summary, "start": e.start.isoformat(), "end": e.end.isoformat()} for e in events]
        
        response_text = self.glm_client.generate_brief(
            events=event_dicts,
            now_iso=datetime.now(timezone.utc).isoformat(),
            timezone=inbound.timezone
        )
        
        return [SchedulerResponse(
            scheduled=False,
            state="calendar_query_answered",
            reason=response_text,
            chat_title=inbound.chat_title,
            source_chat_id=inbound.chat_id,
            source_message_id=inbound.message_id,
        )]

    def _handle_save_memory(
        self,
        inbound: NormalizedInbound,
        entities: dict,
        request_id: str | None = None,
    ) -> list[SchedulerResponse]:
        log_info(logger, "pipeline_save_memory_start", request_id=request_id, entities=entities)
        
        memory_text = entities.get("memory_text") or inbound.raw_text
        category = entities.get("category", "general")
        
        if self.store:
            self.store.ingest_memory(MemoryRow(
                chat_id=inbound.chat_id,
                memory_text=memory_text,
                category=category
            ))
            
        return [SchedulerResponse(
            scheduled=False,
            state="memory_saved",
            reason=f"Got it! I've remembered that: {memory_text}",
            chat_title=inbound.chat_title,
            source_chat_id=inbound.chat_id,
            source_message_id=inbound.message_id,
        )]

    def _handle_general_chat(
        self,
        inbound: NormalizedInbound,
        retrieved_context: list[ContextItem] | None = None,
        request_id: str | None = None,
    ) -> list[SchedulerResponse]:
        log_info(logger, "pipeline_general_chat_start", request_id=request_id)
        
        response_text = self.glm_client.generate_chat_response(
            inbound=inbound,
            context=retrieved_context,
            request_id=request_id
        )
        
        return [SchedulerResponse(
            scheduled=False,
            state="general_chat_replied",
            reason=response_text,
            chat_title=inbound.chat_title,
            source_chat_id=inbound.chat_id,
            source_message_id=inbound.message_id,
        )]
