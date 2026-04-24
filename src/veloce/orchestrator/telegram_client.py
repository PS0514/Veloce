import os
import aiohttp
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning

logger = get_logger(__name__)

class TelegramClient:
    def __init__(self):
        # Prioritize environment variable, then fallback to docker name, then localhost
        self.service_url = os.getenv("TELEGRAM_SERVICE_URL") or os.getenv("VELOCE_TELEGRAM_URL") or "http://telegram_service:8003"
        self.service_url = self.service_url.rstrip("/")
        log_info(logger, "telegram_client_init_remote", service_url=self.service_url)

    async def send_notification(self, text: str, use_bot: bool = True) -> dict:
        url = f"{self.service_url}/send-notification"
        payload = {
            "text": text,
            "use_bot": use_bot
        }
        try:
            log_info(logger, "telegram_client_sending_notification", text_preview=text[:100])
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=20) as response:
                    resp_data = await response.json()
                    if response.status == 200:
                        log_info(logger, "telegram_client_notification_sent", message_id=resp_data.get("message_id"))
                        return resp_data
                    else:
                        log_warning(logger, "telegram_client_notification_failed", status=response.status, response=resp_data)
                        return resp_data
        except Exception as exc:
            log_warning(logger, "telegram_client_notification_error", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def send_message(self, chat_id: int, text: str, reply_to: int = None) -> dict:
        url = f"{self.service_url}/send-message"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_to": reply_to
        }
        try:
            log_info(logger, "telegram_client_sending_message", chat_id=chat_id, text_preview=text[:100])
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=20) as response:
                    resp_data = await response.json()
                    if response.status == 200:
                        log_info(logger, "telegram_client_message_sent", chat_id=chat_id, message_id=resp_data.get("message_id"))
                        return resp_data
                    else:
                        log_warning(logger, "telegram_client_message_failed", status=response.status, response=resp_data, chat_id=chat_id)
                        return resp_data
        except Exception as exc:
            log_warning(logger, "telegram_client_message_error", error=str(exc), chat_id=chat_id)
            return {"status": "error", "error": str(exc)}
