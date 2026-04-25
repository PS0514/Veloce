import base64
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import requests

from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.runtime_config import get_config_value, merge_config_values

logger = get_logger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _clean_html(raw_html: str) -> str:
    """Remove HTML tags and excessive whitespace."""
    # Remove script and style elements
    clean = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all other tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def _get_fresh_google_token() -> str:
    """Get a valid Google access token, refreshing if possible."""
    refresh_token = get_config_value("google_refresh_token") or os.getenv("GOOGLE_REFRESH_TOKEN")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not refresh_token or not client_id or not client_secret:
        return get_config_value("google_access_token") or os.getenv("GOOGLE_ACCESS_TOKEN", "")

    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token", "")).strip()
        if not token:
            return get_config_value("google_access_token") or ""
        
        try:
            merge_config_values({"google_access_token": token})
        except Exception:
            pass
            
        return token
    except Exception as exc:
        log_warning(logger, "google_token_refresh_failed", error=str(exc))
        return get_config_value("google_access_token") or ""


class GmailClient:
    def __init__(self) -> None:
        self.enabled = os.getenv("ENABLE_GMAIL_SYNC", "true").lower() == "true"

    def list_messages(self, query: str = "", max_results: int = 10) -> List[dict]:
        """List messages matching a query."""
        token = _get_fresh_google_token()
        url = f"{GMAIL_BASE_URL.rstrip('/')}/messages"
        params = {"q": query, "maxResults": max_results}
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
            if resp.status_code != 200:
                log_warning(logger, "gmail_api_error", status=resp.status_code, text=resp.text, url=resp.url)
            resp.raise_for_status()
            return resp.json().get("messages", [])
        except Exception as exc:
            log_warning(logger, "gmail_list_failed", error=str(exc))
            raise

    def get_message(self, message_id: str) -> dict:
        """Get full message details."""
        token = _get_fresh_google_token()
        url = f"{GMAIL_BASE_URL.rstrip('/')}/messages/{message_id}"
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code != 200:
                log_warning(logger, "gmail_api_error", status=resp.status_code, text=resp.text, url=resp.url)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log_warning(logger, "gmail_get_failed", error=str(exc), message_id=message_id)
            raise

    def fetch_emails_last_week(self) -> List[dict]:
        """Fetch all emails from the last 7 days."""
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        # Gmail query format: after:YYYY/MM/DD
        query = f"after:{one_week_ago.strftime('%Y/%m/%d')}"
        
        messages = []
        next_page_token = None
        
        while True:
            token = _get_fresh_google_token()
            url = f"{GMAIL_BASE_URL.rstrip('/')}/messages"
            params = {"q": query, "maxResults": 100}
            if next_page_token:
                params["pageToken"] = next_page_token
                
            try:
                resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
                if resp.status_code != 200:
                    log_warning(logger, "gmail_api_error", status=resp.status_code, text=resp.text, url=resp.url)
                resp.raise_for_status()
                data = resp.json()
                
                page_messages = data.get("messages", [])
                for msg_summary in page_messages:
                    full_msg = self.get_message(msg_summary["id"])
                    messages.append(self.parse_message(full_msg))
                
                next_page_token = data.get("nextPageToken")
                if not next_page_token or len(messages) >= 500: # Safety cap
                    break
            except Exception as exc:
                log_warning(logger, "gmail_fetch_last_week_failed", error=str(exc))
                break
                
        return messages

    @staticmethod
    def parse_message(message: dict) -> dict:
        """Parse Gmail message into a simpler format with HTML cleaning and truncation."""
        headers = message.get("payload", {}).get("headers", [])
        
        subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "No Subject")
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Unknown Sender")
        
        # Try to parse date
        try:
            internal_date = int(message.get("internalDate", 0)) / 1000
            date_iso = datetime.fromtimestamp(internal_date, tz=timezone.utc).isoformat()
        except Exception:
            date_iso = datetime.now(timezone.utc).isoformat()

        # Extract body
        body = ""
        html_found = False
        parts = [message.get("payload", {})]
        while parts:
            part = parts.pop(0)
            if part.get("parts"):
                parts.extend(part.get("parts"))
            
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body += base64.urlsafe_b64decode(data).decode("utf-8")
            elif part.get("mimeType") == "text/html":
                html_found = True
                if not body: # Only use HTML if we don't have plain text yet
                    data = part.get("body", {}).get("data", "")
                    if data:
                        raw_html = base64.urlsafe_b64decode(data).decode("utf-8")
                        body = _clean_html(raw_html)

        # Truncation for AI performance (Max 2000 chars)
        if len(body) > 2000:
            body = body[:2000] + "... [TRUNCATED]"

        return {
            "id": message["id"],
            "thread_id": message["threadId"],
            "sender": sender,
            "subject": subject,
            "body": body,
            "date": date_iso,
            "snippet": message.get("snippet", ""),
        }
