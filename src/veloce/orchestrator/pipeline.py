from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from veloce.orchestrator.db import SQLiteStore, ScheduledTaskRow
from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.scheduling_engine import BusyInterval, SchedulingEngine
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
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
                # ... (existing clarification logic)
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
            # Only strategize if the task is confident and not needing clarification
            log_info(logger, "pipeline_strategizing_start", request_id=request_id, task=task.task_name)
            
            # 1. Fetch Energy Settings from Runtime Config
            from veloce.runtime_config import get_config_value
            energy_settings = {
                "deep_work": f"{get_config_value('deep_work_start', '09:00')} - {get_config_value('deep_work_end', '13:00')}",
                "shallow_work": f"{get_config_value('shallow_work_start', '14:00')} - {get_config_value('shallow_work_end', '17:00')}"
            }
            
            # 2. Fetch Workload Context (next 7 days)
            workload_context = []
            try:
                tz = ZoneInfo(inbound.timezone)
                now_local = datetime.now(tz)
                end_of_week = now_local + timedelta(days=7)
                
                raw_events = self.scheduling_engine.calendar_client.list_events(
                    time_min=now_local,
                    time_max=end_of_week
                )
                workload_context = [
                    {"summary": e.summary, "start": e.start.isoformat(), "end": e.end.isoformat()}
                    for e in raw_events
                ]
            except Exception as e:
                log_warning(logger, "pipeline_workload_fetch_failed", error=str(e))

            # 3. Calculate REAL Historical Bias from DB
            historical_bias = "No historical data yet."
            if self.store:
                historical_bias = self.store.calculate_historical_bias()
            
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

                    # Save to persistent DB storage
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

                # For the response, we primarily report on the main task, 
                # but we can list support tasks in the reason.
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
