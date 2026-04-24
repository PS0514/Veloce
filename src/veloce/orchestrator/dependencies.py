from dataclasses import dataclass

from veloce.orchestrator.context_service import ContextService
from veloce.orchestrator.db import SQLiteStore
from veloce.orchestrator.glm_client import GlmClient
from veloce.orchestrator.logging_utils import get_logger, log_info
from veloce.orchestrator.pipeline import SchedulerPipeline
from veloce.orchestrator.scheduling_engine import GoogleCalendarClient, SchedulingEngine
from veloce.orchestrator.telegram_client import TelegramClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class OrchestratorServices:
    store: SQLiteStore
    context_service: ContextService
    glm_client: GlmClient
    calendar_client: GoogleCalendarClient
    scheduling_engine: SchedulingEngine
    pipeline: SchedulerPipeline
    telegram_client: TelegramClient


def build_services(db_path: str) -> OrchestratorServices:
    log_info(logger, "services_build_start", db_path=db_path)
    store = SQLiteStore(db_path)
    context_service = ContextService(store)
    glm_client = GlmClient()
    calendar_client = GoogleCalendarClient()
    scheduling_engine = SchedulingEngine(calendar_client=calendar_client)
    pipeline = SchedulerPipeline(glm_client=glm_client, scheduling_engine=scheduling_engine, store=store)
    telegram_client = TelegramClient()

    log_info(
        logger,
        "services_build_done",
        db_path=db_path,
        glm_model=glm_client.model,
        google_sync_enabled=calendar_client.enabled,
        calendar_id=calendar_client.calendar_id,
    )

    return OrchestratorServices(
        store=store,
        context_service=context_service,
        glm_client=glm_client,
        calendar_client=calendar_client,
        scheduling_engine=scheduling_engine,
        pipeline=pipeline,
        telegram_client=telegram_client,
    )
