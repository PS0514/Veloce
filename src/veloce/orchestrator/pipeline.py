from datetime import datetime, timezone

from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.logging_utils import get_logger, log_info
from veloce.orchestrator.models import (
    ContextItem,
    NormalizedInbound,
    SchedulerInbound,
    SchedulerResponse,
    TaskCandidate,
)
from veloce.orchestrator.scheduling_engine import SchedulingEngine

logger = get_logger(__name__)


class SchedulerPipeline:
    def __init__(
        self,
        glm_client: GlmClient,
        scheduling_engine: SchedulingEngine,
        min_confidence_for_auto: float = 0.7,
    ) -> None:
        self.glm_client = glm_client
        self.scheduling_engine = scheduling_engine
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
        log_info(
            logger,
            "pipeline_glm_extract_start",
            request_id=request_id,
            source=inbound.source,
            chat_id=inbound.chat_id,
        )
        extraction = self.glm_client.extract_tasks(inbound, request_id=request_id)
        log_info(
            logger,
            "pipeline_glm_extract_done",
            request_id=request_id,
            tasks=len(extraction.tasks),
            metadata=extraction.metadata,
        )

        selected = self._select_task(extraction.tasks)
        if selected is not None:
            log_info(
                logger,
                "pipeline_task_selected",
                request_id=request_id,
                task=selected.task_name,
                deadline=selected.deadline_iso,
                confidence=round(selected.confidence, 3),
                needs_clarification=selected.needs_clarification,
            )
        else:
            log_info(logger, "pipeline_task_selected", request_id=request_id, task=None)

        if selected is None:
            return SchedulerResponse(
                scheduled=False,
                reason="No actionable task extracted",
                selected_task=None,
                state="decision_no_action",
            )

        if selected.needs_clarification or selected.confidence < self.min_confidence_for_auto:
            question = selected.clarification_question or "I need a bit more detail before scheduling this."
            logger.info(
                "pipeline_needs_clarification request_id=%s reason=confidence_or_flag confidence=%.2f min=%.2f",
                request_id,
                selected.confidence,
                self.min_confidence_for_auto,
            )
            return SchedulerResponse(
                scheduled=False,
                selected_task=selected,
                needs_clarification=True,
                clarification_question=question,
                reason="Needs clarification before scheduling",
                state="needs_clarification",
            )

        if retrieved_context is not None and len(retrieved_context) == 0:
            log_info(
                logger,
                "pipeline_needs_context",
                request_id=request_id,
                reason="empty_retrieved_context",
            )
            return SchedulerResponse(
                scheduled=False,
                selected_task=selected,
                needs_clarification=True,
                clarification_question="I could not find enough context. Can you confirm the exact deadline and duration?",
                reason="More context needed",
                state="decision_needs_context",
            )

        schedule_result = self.scheduling_engine.schedule(
            task=selected,
            timezone_name=inbound.timezone,
            request_id=request_id,
        )
        log_info(
            logger,
            "pipeline_schedule_done",
            request_id=request_id,
            state=schedule_result.state,
            scheduled=schedule_result.scheduled,
            reason=schedule_result.reason,
            calendar_event_id=schedule_result.calendar_event_id,
            calendar_link=schedule_result.calendar_link,
            proposed_start=schedule_result.proposed_start,
            proposed_end=schedule_result.proposed_end,
        )
        return SchedulerResponse(
            scheduled=schedule_result.scheduled,
            selected_task=selected,
            reason=schedule_result.reason,
            state=schedule_result.state,
            calendar_event_id=schedule_result.calendar_event_id,
            calendar_link=schedule_result.calendar_link,
            needs_clarification=schedule_result.needs_clarification,
            clarification_question=schedule_result.clarification_question,
        )
