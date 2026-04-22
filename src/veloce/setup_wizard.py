import os
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlencode

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError

from veloce.config import load_listener_config
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning
from veloce.runtime_config import load_runtime_config, merge_config_values, get_config_value

logger = get_logger(__name__)

APP = Flask(__name__)
APP.secret_key = os.getenv("FLASK_SECRET_KEY", "veloce-local-setup-secret")

ROOT = Path(__file__).resolve().parents[2]
GOOGLE_CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_REDIRECT_PATH = "/google/oauth/callback"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
TELEGRAM_LOGIN_SESSION_KEY = "telegram_login_state"


# ---------------------------------------------------------------------------
# Helpers — read-only access to .env values via os.getenv
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    """Read a value from the environment (.env loaded by dotenv at startup)."""
    return os.getenv(key, default).strip()


def current_values() -> dict[str, str | bool]:
    """Build a snapshot of all values the UI needs.

    Static values come from os.getenv (i.e. .env — read-only).
    Mutable values come from veloce_config.json.
    """
    cfg = load_runtime_config()
    return {
        # Static from .env (read-only, displayed but not editable)
        "telegram_api_id": _env("TELEGRAM_API_ID"),
        "telegram_api_hash": _env("TELEGRAM_API_HASH"),
        "google_client_id": _env("GOOGLE_CLIENT_ID"),
        "google_client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "enable_google_sync": _env("ENABLE_GOOGLE_SYNC", "false").lower() == "true",
        # Mutable from config file (editable via UI)
        "google_access_token": cfg.get("google_access_token", ""),
        "google_refresh_token": cfg.get("google_refresh_token", ""),
        "google_calendar_id": cfg.get("google_calendar_id", _env("GOOGLE_CALENDAR_ID", "primary")),
        "telegram_channels": cfg.get("telegram_channel_filters", _env("TELEGRAM_CHANNEL_FILTERS")),
        "listener_keywords": cfg.get("listener_keywords", _env("LISTENER_KEYWORDS")),
        # Transient UI state (not persisted)
        "telegram_phone": "",
        "telegram_code": "",
        "telegram_password": "",
    }


# ---------------------------------------------------------------------------
# Config persistence — writes ONLY to veloce_config.json, never .env
# ---------------------------------------------------------------------------

def save_settings(values: dict[str, str | bool]) -> None:
    """Persist the mutable settings to veloce_config.json."""
    merge_config_values({
        "telegram_channel_filters": str(values.get("telegram_channels", "")),
        "listener_keywords": str(values.get("listener_keywords", "")),
        "google_calendar_id": str(values.get("google_calendar_id", "primary")),
        "google_access_token": str(values.get("google_access_token", "")),
        "google_refresh_token": str(values.get("google_refresh_token", "")),
    })


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

def get_google_redirect_uri() -> str:
    return request.url_root.rstrip("/") + GOOGLE_REDIRECT_PATH


def _require_google_credentials() -> tuple[str, str]:
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env "
            "before using Google login."
        )
    return client_id, client_secret


@APP.route("/google/oauth/start")
def google_oauth_start():
    client_id, _client_secret = _require_google_credentials()
    state = token_urlsafe(24)
    session["google_oauth_state"] = state

    params = {
        "client_id": client_id,
        "redirect_uri": get_google_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "state": state,
        "include_granted_scopes": "true",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@APP.route("/google/oauth/callback", methods=["GET", "POST"])
def google_oauth_callback():
    expected_state = session.get("google_oauth_state")
    received_state = request.values.get("state", "")
    if not expected_state or received_state != expected_state:
        abort(400, description="Invalid Google OAuth state")

    error = request.values.get("error")
    if error:
        return _render(error=f"Google login failed: {error}")

    code = request.values.get("code", "").strip()
    if not code:
        abort(400, description="Missing Google OAuth code")

    client_id, client_secret = _require_google_credentials()

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": get_google_redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    access_token = str(payload.get("access_token", "")).strip()
    refresh_token = str(payload.get("refresh_token", "")).strip()
    token_type = str(payload.get("token_type", "")).strip()
    expires_in = str(payload.get("expires_in", "")).strip()
    if not access_token:
        raise RuntimeError("Google OAuth token exchange did not return an access token.")

    # Save tokens to config file (not .env)
    merge_config_values({
        "google_access_token": access_token,
        "google_refresh_token": refresh_token or get_config_value("google_refresh_token"),
    })
    session.pop("google_oauth_state", None)

    message_bits = ["Google login completed successfully."]
    if token_type:
        message_bits.append(f"Token type: {token_type}")
    if expires_in:
        message_bits.append(f"Expires in: {expires_in} seconds")

    return _render(
        success=" ".join(message_bits),
        google_info="You can now list calendars from the authenticated account.",
    )


def _get_fresh_google_token() -> str:
    """Get a valid Google access token, refreshing if possible."""
    refresh_token = get_config_value("google_refresh_token") or _env("GOOGLE_REFRESH_TOKEN")
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")

    # Always try refresh if we have the credentials
    if refresh_token and client_id and client_secret:
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
            raise RuntimeError("Google token refresh did not return an access token.")
        # Persist the fresh token
        merge_config_values({"google_access_token": token})
        return token

    # Fall back to stored access token (may be expired)
    stored = get_config_value("google_access_token") or _env("GOOGLE_ACCESS_TOKEN")
    if stored:
        return stored

    raise RuntimeError(
        "Google Access Token or Refresh Token + Client ID + Client Secret "
        "is required to list calendars."
    )


def list_google_calendars() -> list[dict[str, str]]:
    token = _get_fresh_google_token()
    response = requests.get(
        GOOGLE_CALENDAR_LIST_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"minAccessRole": "writer"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    calendars = payload.get("items", []) if isinstance(payload, dict) else []

    results: list[dict[str, str]] = []
    for calendar in calendars:
        if not isinstance(calendar, dict):
            continue
        calendar_id = str(calendar.get("id", "")).strip()
        summary = str(calendar.get("summary", calendar_id or "Calendar")).strip()
        description = str(calendar.get("description", "")).strip()
        primary = bool(calendar.get("primary", False))
        access_role = str(calendar.get("accessRole", "")).strip()
        if not calendar_id:
            continue

        label_bits = [summary]
        if primary:
            label_bits.append("primary")
        if access_role:
            label_bits.append(access_role)
        if description:
            label_bits.append(description)

        results.append(
            {
                "id": calendar_id,
                "summary": summary,
                "description": description,
                "label": " | ".join(label_bits),
                "primary": "true" if primary else "false",
                "access_role": access_role,
            }
        )

    results.sort(key=lambda item: (item["primary"] != "true", item["summary"].lower()))
    return results


# ---------------------------------------------------------------------------
# Telegram login
# ---------------------------------------------------------------------------

def clear_telegram_login_state() -> None:
    session.pop(TELEGRAM_LOGIN_SESSION_KEY, None)


def get_telegram_login_state() -> dict[str, str]:
    state = session.get(TELEGRAM_LOGIN_SESSION_KEY, {})
    return state if isinstance(state, dict) else {}


def start_telegram_web_login(phone: str) -> str:
    api_id = _env("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env.")

    config = load_listener_config()
    client = TelegramClient(config.session_path, api_id, api_hash)
    client.connect()
    try:
        if client.is_user_authorized():
            me = client.get_me()
            display_name = (getattr(me, "first_name", "") or "").strip() or getattr(me, "username", "") or "Telegram user"
            clear_telegram_login_state()
            return f"Already logged in as {display_name}."

        result = client.send_code_request(phone)
        session[TELEGRAM_LOGIN_SESSION_KEY] = {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": result.phone_code_hash,
            "awaiting": "code",
        }
        return f"Verification code sent to {phone}. Enter the code below."
    finally:
        client.disconnect()


def complete_telegram_web_login(code: str, password: str) -> tuple[bool, str]:
    state = get_telegram_login_state()
    api_id = state.get("api_id", "").strip()
    api_hash = state.get("api_hash", "").strip()
    phone = state.get("phone", "").strip()
    phone_code_hash = state.get("phone_code_hash", "").strip()
    awaiting = state.get("awaiting", "").strip()

    if not (api_id and api_hash and phone and phone_code_hash):
        clear_telegram_login_state()
        raise RuntimeError("No Telegram login is in progress. Start login first.")

    config = load_listener_config()
    client = TelegramClient(config.session_path, api_id, api_hash)
    client.connect()
    try:
        if awaiting == "password":
            if not password:
                return False, "Enter your Telegram 2FA password to finish login."
            client.sign_in(password=password)
        else:
            if not code:
                return False, "Enter the Telegram verification code sent to your phone."
            try:
                client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                state["awaiting"] = "password"
                session[TELEGRAM_LOGIN_SESSION_KEY] = state
                return False, "2FA password required. Enter your Telegram password below."

        if not client.is_user_authorized():
            return False, "Telegram login did not complete. Try again."

        me = client.get_me()
        phone_value = getattr(me, "phone", "")
        first_name = getattr(me, "first_name", "")
        last_name = getattr(me, "last_name", "")
        username = getattr(me, "username", "")

        name_parts = [first_name, last_name]
        display_name = " ".join(part for part in name_parts if part).strip()
        if not display_name and username:
            display_name = f"@{username}"
        if not display_name and phone_value:
            display_name = phone_value
        if not display_name:
            display_name = "Telegram user"

        clear_telegram_login_state()
        return True, f"Telegram login completed. Logged in as {display_name}."
    finally:
        client.disconnect()


def is_telegram_authenticated() -> bool:
    """Check if Telegram session exists and is authenticated."""
    config = load_listener_config()
    session_file = Path(f"{config.session_path}.session")
    return session_file.exists()


def get_telegram_user_info(api_id: str | None = None, api_hash: str | None = None) -> tuple[bool, str]:
    """Get authenticated Telegram user info if available."""
    if not is_telegram_authenticated():
        return False, "Not logged in"
    
    try:
        config = load_listener_config()
        effective_api_id = (api_id or config.api_id or _env("TELEGRAM_API_ID")).strip()
        effective_api_hash = (api_hash or config.api_hash or _env("TELEGRAM_API_HASH")).strip()
        if not effective_api_id or not effective_api_hash:
            return False, "Missing Telegram API credentials"

        client = TelegramClient(config.session_path, effective_api_id, effective_api_hash)
        client.connect()
        try:
            if not client.is_user_authorized():
                return False, "Session exists but not authorized"
            
            me = client.get_me()
            phone = getattr(me, "phone", None)
            first_name = getattr(me, "first_name", "")
            last_name = getattr(me, "last_name", "")
            username = getattr(me, "username", "")
            
            name_parts = [first_name, last_name]
            display_name = " ".join(p for p in name_parts if p).strip()
            if not display_name and username:
                display_name = f"@{username}"
            if not display_name:
                display_name = phone or "Unknown"
            
            return True, display_name
        finally:
            client.disconnect()
    except Exception as exc:
        return False, f"Error checking status: {str(exc)[:50]}"


def list_user_channels() -> list[dict[str, str]]:
    api_id = _env("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env.")

    config = load_listener_config()
    channels: list[dict[str, str]] = []
    client = TelegramClient(config.session_path, api_id, api_hash)
    client.connect()
    try:
        if not client.is_user_authorized():
            raise RuntimeError("Telegram session is not authenticated. Complete Telegram login first.")

        for dialog in client.iter_dialogs():
            entity = dialog.entity
            title = (dialog.name or "").strip() or f"Chat {dialog.id}"
            username = getattr(entity, "username", None)
            value = f"@{username}" if username else str(dialog.id)
            last_message_dt = getattr(dialog, "date", None)
            if isinstance(last_message_dt, datetime):
                last_message_ts = int(last_message_dt.timestamp())
                last_message_human = last_message_dt.strftime("%Y-%m-%d %H:%M")
            else:
                last_message_ts = 0
                last_message_human = "No messages yet"

            label = f"{title} ({value})"
            channels.append(
                {
                    "value": value,
                    "label": label,
                    "search_text": f"{title} {value}".lower(),
                    "last_message_ts": str(last_message_ts),
                    "last_message_human": last_message_human,
                }
            )
    finally:
        client.disconnect()

    channels.sort(key=lambda item: int(item["last_message_ts"]), reverse=True)
    return channels


def try_auto_load_channels() -> tuple[list[dict[str, str]], str]:
    api_id = _env("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        return [], ""

    try:
        channels = list_user_channels()
        return channels, f"Auto-loaded {len(channels)} channels/chats from your Telegram session."
    except Exception as exc:
        message = str(exc).lower()
        if "not authenticated" in message or "authorized" in message:
            return [], "Telegram session is not authenticated yet. Complete Telegram login first to auto-load channels."
        return [], ""


def try_auto_load_google_calendars() -> tuple[list[dict[str, str]], str]:
    if not _env("ENABLE_GOOGLE_SYNC", "false").lower() == "true":
        return [], ""

    try:
        calendars = list_google_calendars()
        if not calendars:
            return [], "No Google calendars were returned for the current account."
        return calendars, f"Loaded {len(calendars)} Google calendar(s). Select one as the scheduler target."
    except Exception as exc:
        return [], f"Google calendar lookup skipped or failed: {exc}"


def get_google_connection_status(values: dict[str, str | bool]) -> tuple[bool, str]:
    if not bool(values.get("enable_google_sync")):
        return False, "Google sync is disabled in setup."

    try:
        calendars = list_google_calendars()
        count = len(calendars)
        if count > 0:
            return True, f"Connected ({count} calendar(s) available)."
        return True, "Connected."
    except Exception as exc:
        return False, f"Not connected: {exc}"


@APP.route("/auth/status", methods=["GET"])
def auth_status():
    values = current_values()
    google_connected, google_status = get_google_connection_status(values)
    telegram_connected, telegram_user = get_telegram_user_info(
        str(values.get("telegram_api_id", "")).strip(),
        str(values.get("telegram_api_hash", "")).strip(),
    )
    telegram_status = (
        f"Connected as {telegram_user}." if telegram_connected else f"Not connected: {telegram_user}"
    )

    return jsonify(
        {
            "google_connected": google_connected,
            "telegram_connected": telegram_connected,
            "account_ready": google_connected and telegram_connected,
            "google_status": google_status,
            "telegram_status": telegram_status,
        }
    )


# ---------------------------------------------------------------------------
# Docker compose helpers (development)
# ---------------------------------------------------------------------------

def compose_base_command() -> list[str]:
    deploy_compose = ROOT / "deploy" / "docker-compose.yaml"
    if deploy_compose.exists():
        return ["docker", "compose", "-f", str(deploy_compose)]
    return ["docker", "compose"]


def run_compose(args: list[str]) -> str:
    command = compose_base_command() + args
    log_info(logger, "setup_compose_start", command=" ".join(command))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        log_warning(
            logger,
            "setup_compose_failed",
            command=" ".join(command),
            returncode=result.returncode,
            output_preview=output[:240],
        )
        raise RuntimeError(output or "Docker compose command failed.")
    log_info(logger, "setup_compose_done", command=" ".join(command), output_preview=output[:240])
    return output


def start_docker_stack() -> None:
    run_compose(["up", "-d"])


def stop_docker_stack() -> None:
    run_compose(["stop"])


def restart_docker_stack() -> None:
    # Recreate containers so updated env values are applied.
    run_compose(["up", "-d", "--force-recreate"])


def get_services_status() -> tuple[list[dict[str, str]], str]:
    try:
        services_output = run_compose(["config", "--services"])
        all_services = [line.strip() for line in services_output.splitlines() if line.strip()]

        running_output = run_compose(["ps", "--services", "--filter", "status=running"])
        running = {line.strip() for line in running_output.splitlines() if line.strip()}

        statuses: list[dict[str, str]] = []
        for service in all_services:
            state = "running" if service in running else "stopped"
            statuses.append({"name": service, "state": state})

        if not statuses:
            return [], "No services found in compose config."

        running_count = sum(1 for svc in statuses if svc["state"] == "running")
        summary = f"{running_count}/{len(statuses)} services running"
        return statuses, summary
    except Exception as exc:
        return [], f"Service status unavailable: {exc}"


# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------

def _render(*, success="", error="", info="", google_info="",
            channels=None, google_calendars=None,
            telegram_info=None, telegram_login_state=None,
            service_statuses=None, services_summary=""):
    """Shorthand to render the wizard template with all required variables."""
    if channels is None:
        channels = []
    if google_calendars is None:
        google_calendars = []
    if service_statuses is None:
        service_statuses = []
    if telegram_login_state is None:
        telegram_login_state = {}
    if telegram_info is None:
        telegram_info = "Telegram not logged in. Click Sign in with Telegram."

    return render_template(
        "setup_wizard.html",
        values=current_values(),
        channels=channels,
        google_calendars=google_calendars,
        success=success,
        error=error,
        info=info,
        telegram_info=telegram_info,
        telegram_login_state=telegram_login_state,
        google_info=google_info,
        service_statuses=service_statuses,
        services_summary=services_summary,
    )


# ---------------------------------------------------------------------------
# Main route
# ---------------------------------------------------------------------------

@APP.route("/", methods=["GET", "POST"])
def index():
    values = current_values()
    channels: list[dict[str, str]] = []
    google_calendars: list[dict[str, str]] = []
    success = ""
    error = ""
    info = ""
    telegram_info = "Telegram not logged in. Click Sign in with Telegram."
    service_statuses, services_summary = get_services_status()
    google_info = ""
    telegram_flow_info = ""
    telegram_login_state = get_telegram_login_state()

    # Check Telegram auth status
    is_telegram_authed, telegram_user = get_telegram_user_info(
        str(values.get("telegram_api_id", "")).strip(),
        str(values.get("telegram_api_hash", "")).strip(),
    )
    if is_telegram_authed:
        telegram_info = f"✓ Logged in as {telegram_user}"
    
    if request.method == "POST":
        action = request.form.get("action", "save")
        log_info(logger, "setup_action_received", action=action)

        # Collect mutable form values
        values.update(
            {
                "telegram_channels": request.form.get("telegram_channels", "").strip(),
                "listener_keywords": request.form.get("listener_keywords", "").strip(),
                "google_calendar_id": request.form.get("google_calendar_id", "primary").strip() or "primary",
                "telegram_phone": request.form.get("telegram_phone", "").strip(),
                "telegram_code": request.form.get("telegram_code", "").strip(),
                "telegram_password": request.form.get("telegram_password", "").strip(),
            }
        )

        try:
            if action == "telegram_login":
                phone = str(values["telegram_phone"]).strip()
                if not phone:
                    raise RuntimeError("Telegram phone number is required (example: +60123456789).")
                telegram_flow_info = start_telegram_web_login(phone)

            elif action == "telegram_verify_code":
                success_login, message = complete_telegram_web_login(
                    str(values["telegram_code"]).strip(),
                    str(values["telegram_password"]).strip(),
                )
                if success_login:
                    success = message
                else:
                    telegram_flow_info = message

            elif action == "telegram_cancel_login":
                clear_telegram_login_state()
                telegram_flow_info = "Telegram login flow cancelled."

            elif action == "list_channels":
                channels = list_user_channels()
                success = f"Loaded {len(channels)} channels/chats from Telegram."

            elif action == "list_google_calendars":
                google_calendars = list_google_calendars()
                if google_calendars and (not values["google_calendar_id"] or values["google_calendar_id"] == "primary"):
                    values["google_calendar_id"] = google_calendars[0]["id"]
                success = f"Loaded {len(google_calendars)} Google calendars."

            elif action == "save":
                save_settings(values)
                success = "Settings saved to config file."

            elif action == "services_start":
                start_docker_stack()
                success = "Docker services started."
            elif action == "services_stop":
                stop_docker_stack()
                success = "Docker services stopped."
            elif action == "services_restart":
                restart_docker_stack()
                success = "Docker services restarted with recreated containers."
            elif action == "services_refresh":
                success = "Service status refreshed."
            else:
                error = f"Unknown action: {action}"

        except Exception as exc:
            error = f"Setup failed: {exc}"
            log_warning(logger, "setup_action_failed", action=action, error=exc)

        if not error:
            log_info(logger, "setup_action_done", action=action, success=success or None, info=info or None)

        service_statuses, services_summary = get_services_status()

        if action != "list_channels":
            channels, auto_info = try_auto_load_channels()
            if auto_info and not info and not telegram_flow_info:
                info = auto_info
        google_calendars, google_info = try_auto_load_google_calendars()
        telegram_login_state = get_telegram_login_state()
        
        # Re-check Telegram status after login
        is_telegram_authed, telegram_user = get_telegram_user_info(
            str(values.get("telegram_api_id", "")).strip(),
            str(values.get("telegram_api_hash", "")).strip(),
        )
        if is_telegram_authed:
            telegram_info = f"✓ Logged in as {telegram_user}"
        else:
            telegram_info = "Telegram not logged in. Click Sign in with Telegram."
    else:
        channels, info = try_auto_load_channels()
        google_calendars, google_info = try_auto_load_google_calendars()

    if telegram_flow_info:
        info = telegram_flow_info

    return render_template(
        "setup_wizard.html",
        values=values,
        channels=channels,
        google_calendars=google_calendars,
        success=success,
        error=error,
        info=info,
        telegram_info=telegram_info,
        telegram_login_state=telegram_login_state,
        google_info=google_info,
        service_statuses=service_statuses,
        services_summary=services_summary,
    )


def launch_browser() -> None:
    webbrowser.open("http://127.0.0.1:8765")


def run_setup_wizard() -> None:
    log_info(logger, "setup_wizard_start", url="http://127.0.0.1:8765")
    threading.Timer(1.0, launch_browser).start()
    APP.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    run_setup_wizard()
