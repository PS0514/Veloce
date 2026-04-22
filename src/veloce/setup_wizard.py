import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlencode

import requests
from flask import Flask, abort, redirect, render_template, request, session, url_for
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError

from veloce.config import load_listener_config
from veloce.orchestrator.logging_utils import get_logger, log_info, log_warning

logger = get_logger(__name__)

APP = Flask(__name__)
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WEBHOOK = "http://n8n:5678/webhook/telegram"
GOOGLE_CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_REDIRECT_PATH = "/google/oauth/callback"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
TELEGRAM_LOGIN_SESSION_KEY = "telegram_login_state"


def to_bool(value: bool) -> str:
    return "true" if value else "false"


def read_env_file() -> dict[str, str]:
    env_map: dict[str, str] = {}
    if not ENV_PATH.exists():
        return env_map

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        env_map[key.strip()] = val.strip()

    return env_map


APP.secret_key = read_env_file().get("FLASK_SECRET_KEY", "veloce-local-setup-secret")


def default_values() -> dict[str, str | bool]:
    env_map = read_env_file()
    orchestrator_url = env_map.get("VELOCE_ORCHESTRATOR_URL", "http://127.0.0.1:8000/veloce-task-scheduler")
    return {
        "telegram_api_id": env_map.get("TELEGRAM_API_ID", ""),
        "telegram_api_hash": env_map.get("TELEGRAM_API_HASH", ""),
        "n8n_webhook_url": env_map.get("N8N_WEBHOOK_URL", orchestrator_url),
        "generic_timezone": env_map.get("GENERIC_TIMEZONE", "Asia/Kuala_Lumpur"),
        "veloce_orchestrator_url": orchestrator_url,
        "veloce_db_path": env_map.get("VELOCE_DB_PATH", "data/veloce.db"),
        "google_calendar_id": env_map.get("GOOGLE_CALENDAR_ID", "primary"),
        "google_client_id": env_map.get("GOOGLE_CLIENT_ID", ""),
        "google_client_secret": env_map.get("GOOGLE_CLIENT_SECRET", ""),
        "google_access_token": env_map.get("GOOGLE_ACCESS_TOKEN", ""),
        "google_refresh_token": env_map.get("GOOGLE_REFRESH_TOKEN", ""),
        "telegram_channels": env_map.get("TELEGRAM_CHANNEL_FILTERS", ""),
        "listener_keywords": env_map.get("LISTENER_KEYWORDS", ""),
        "enable_google_sync": env_map.get("ENABLE_GOOGLE_SYNC", "false").lower() == "true",
        "start_docker": True,
        "telegram_phone": "",
        "telegram_code": "",
        "telegram_password": "",
    }


def save_env(values: dict[str, str | bool]) -> None:
    target_url = str(values["n8n_webhook_url"]).strip()
    lines = [
        f"TELEGRAM_API_ID={values['telegram_api_id']}",
        f"TELEGRAM_API_HASH={values['telegram_api_hash']}",
        f"N8N_WEBHOOK_URL={target_url}",
        f"VELOCE_ORCHESTRATOR_URL={target_url}",
        f"VELOCE_DB_PATH={values['veloce_db_path']}",
        f"GENERIC_TIMEZONE={values['generic_timezone']}",
        f"ENABLE_GOOGLE_SYNC={to_bool(bool(values['enable_google_sync']))}",
        f"GOOGLE_CALENDAR_ID={values['google_calendar_id']}",
        f"GOOGLE_CLIENT_ID={values['google_client_id']}",
        f"GOOGLE_CLIENT_SECRET={values['google_client_secret']}",
        f"GOOGLE_ACCESS_TOKEN={values['google_access_token']}",
        f"GOOGLE_REFRESH_TOKEN={values['google_refresh_token']}",
        f"TELEGRAM_CHANNEL_FILTERS={values['telegram_channels']}",
        f"LISTENER_KEYWORDS={values['listener_keywords']}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge_env_updates(updates: dict[str, str | bool]) -> None:
    current = read_env_file()
    current.update({key: str(value) for key, value in updates.items()})
    lines = [f"{key}={value}" for key, value in current.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_google_redirect_uri() -> str:
    return request.url_root.rstrip("/") + GOOGLE_REDIRECT_PATH


def get_google_oauth_config(values: dict[str, str | bool]) -> tuple[str, str]:
    client_id = str(values.get("google_client_id", "")).strip()
    client_secret = str(values.get("google_client_secret", "")).strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google Client ID and Client Secret are required for browser login.")
    return client_id, client_secret


@APP.route("/google/oauth/start")
def google_oauth_start():
    values = default_values()
    client_id, _client_secret = get_google_oauth_config(values)
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


@APP.route("/google/oauth/callback")
def google_oauth_callback():
    expected_state = session.get("google_oauth_state")
    received_state = request.args.get("state", "")
    if not expected_state or received_state != expected_state:
        abort(400, description="Invalid Google OAuth state")

    error = request.args.get("error")
    if error:
        return render_template("setup_wizard.html", values=default_values(), channels=[], google_calendars=[], success="", error=f"Google login failed: {error}", info="", google_info="", service_statuses=[], services_summary="")

    code = request.args.get("code", "").strip()
    if not code:
        abort(400, description="Missing Google OAuth code")

    values = default_values()
    client_id, client_secret = get_google_oauth_config(values)

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

    merge_env_updates(
        {
            "ENABLE_GOOGLE_SYNC": "true",
            "GOOGLE_ACCESS_TOKEN": access_token,
            "GOOGLE_REFRESH_TOKEN": refresh_token or values.get("google_refresh_token", ""),
        }
    )
    session.pop("google_oauth_state", None)

    message_bits = ["Google login completed successfully."]
    if token_type:
        message_bits.append(f"Token type: {token_type}")
    if expires_in:
        message_bits.append(f"Expires in: {expires_in} seconds")

    return render_template(
        "setup_wizard.html",
        values=default_values(),
        channels=[],
        google_calendars=[],
        success=" ".join(message_bits),
        error="",
        info="",
        google_info="You can now list calendars from the authenticated account.",
        service_statuses=[],
        services_summary="",
    )


def get_google_access_token(values: dict[str, str | bool]) -> str:
    access_token = str(values.get("google_access_token", "")).strip()
    if access_token:
        return access_token

    refresh_token = str(values.get("google_refresh_token", "")).strip()
    client_id = str(values.get("google_client_id", "")).strip()
    client_secret = str(values.get("google_client_secret", "")).strip()
    if not (refresh_token and client_id and client_secret):
        raise RuntimeError("Google Access Token or Refresh Token + Client ID + Client Secret is required to list calendars.")

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
    return token


def list_google_calendars(values: dict[str, str | bool]) -> list[dict[str, str]]:
    token = get_google_access_token(values)
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


def clear_telegram_login_state() -> None:
    session.pop(TELEGRAM_LOGIN_SESSION_KEY, None)


def get_telegram_login_state() -> dict[str, str]:
    state = session.get(TELEGRAM_LOGIN_SESSION_KEY, {})
    return state if isinstance(state, dict) else {}


def start_telegram_web_login(api_id: str, api_hash: str, phone: str) -> str:
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


def get_telegram_user_info() -> tuple[bool, str]:
    """Get authenticated Telegram user info if available."""
    if not is_telegram_authenticated():
        return False, "Not logged in"
    
    try:
        config = load_listener_config()
        client = TelegramClient(config.session_path, config.api_id, config.api_hash)
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


def list_user_channels(api_id: str, api_hash: str) -> list[dict[str, str]]:
    config = load_listener_config()
    channels: list[dict[str, str]] = []
    client = TelegramClient(config.session_path, api_id, api_hash)
    client.connect()
    try:
        if not client.is_user_authorized():
            raise RuntimeError("Telegram session is not authenticated. Enable Telegram login and save once first.")

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


def try_auto_load_channels(values: dict[str, str | bool]) -> tuple[list[dict[str, str]], str]:
    api_id = str(values.get("telegram_api_id", "")).strip()
    api_hash = str(values.get("telegram_api_hash", "")).strip()
    if not api_id or not api_hash:
        return [], ""

    try:
        channels = list_user_channels(api_id, api_hash)
        return channels, f"Auto-loaded {len(channels)} channels/chats from your Telegram session."
    except Exception as exc:
        message = str(exc).lower()
        if "not authenticated" in message or "authorized" in message:
            return [], "Telegram session is not authenticated yet. Enable Telegram login and save once to auto-load channels."
        return [], ""


def try_auto_load_google_calendars(values: dict[str, str | bool]) -> tuple[list[dict[str, str]], str]:
    if not bool(values.get("enable_google_sync")):
        return [], ""

    try:
        calendars = list_google_calendars(values)
        if not calendars:
            return [], "No Google calendars were returned for the current account."
        return calendars, f"Loaded {len(calendars)} Google calendar(s). Select one as the scheduler target."
    except Exception as exc:
        return [], f"Google calendar lookup skipped or failed: {exc}"


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


@APP.route("/", methods=["GET", "POST"])
def index():
    values = default_values()
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
    is_telegram_authed, telegram_user = get_telegram_user_info()
    if is_telegram_authed:
        telegram_info = f"✓ Logged in as {telegram_user}"
    
    if request.method == "POST":
        action = request.form.get("action", "save")
        log_info(logger, "setup_action_received", action=action)
        values.update(
            {
                "telegram_api_id": request.form.get("telegram_api_id", "").strip(),
                "telegram_api_hash": request.form.get("telegram_api_hash", "").strip(),
                "n8n_webhook_url": request.form.get("n8n_webhook_url", DEFAULT_WEBHOOK).strip() or DEFAULT_WEBHOOK,
                "generic_timezone": request.form.get("generic_timezone", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur",
                "veloce_db_path": request.form.get("veloce_db_path", "data/veloce.db").strip() or "data/veloce.db",
                "google_calendar_id": request.form.get("google_calendar_id", "primary").strip() or "primary",
                "google_client_id": request.form.get("google_client_id", "").strip(),
                "google_client_secret": request.form.get("google_client_secret", "").strip(),
                "google_access_token": request.form.get("google_access_token", "").strip(),
                "google_refresh_token": request.form.get("google_refresh_token", "").strip(),
                "telegram_channels": request.form.get("telegram_channels", "").strip(),
                "listener_keywords": request.form.get("listener_keywords", "").strip(),
                "enable_google_sync": bool(request.form.get("enable_google_sync")),
                "start_docker": bool(request.form.get("start_docker")),
                "telegram_phone": request.form.get("telegram_phone", "").strip(),
                "telegram_code": request.form.get("telegram_code", "").strip(),
                "telegram_password": request.form.get("telegram_password", "").strip(),
            }
        )

        actions_requiring_telegram = {"save", "save_restart", "list_channels"}
        needs_telegram = action in actions_requiring_telegram

        if needs_telegram and (not values["telegram_api_id"] or not values["telegram_api_hash"]):
            error = "Telegram API ID and API Hash are required."
        else:
            try:
                if action == "telegram_login":
                    api_id = str(values["telegram_api_id"]).strip()
                    api_hash = str(values["telegram_api_hash"]).strip()
                    phone = str(values["telegram_phone"]).strip()
                    if not api_id or not api_hash:
                        raise RuntimeError("Telegram API ID and API Hash are required to login.")
                    if not phone:
                        raise RuntimeError("Telegram phone number is required (example: +60123456789).")
                    telegram_flow_info = start_telegram_web_login(api_id, api_hash, phone)
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
                    channels = list_user_channels(str(values["telegram_api_id"]), str(values["telegram_api_hash"]))
                    success = f"Loaded {len(channels)} channels/chats from Telegram."
                elif action == "list_google_calendars":
                    google_calendars = list_google_calendars(values)
                    if google_calendars and (not values["google_calendar_id"] or values["google_calendar_id"] == "primary"):
                        values["google_calendar_id"] = google_calendars[0]["id"]
                    success = f"Loaded {len(google_calendars)} Google calendars."
                elif action in {"save", "save_restart"}:
                    save_env(values)

                    if action == "save_restart":
                        restart_docker_stack()
                        success = "Saved configuration and restarted Docker services with recreated containers."
                    elif values["start_docker"]:
                        start_docker_stack()
                        success = "Saved configuration and started Docker services."
                    else:
                        success = "Saved configuration. Services were not changed."
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
            channels, auto_info = try_auto_load_channels(values)
            if auto_info and not info and not telegram_flow_info:
                info = auto_info
        google_calendars, google_info = try_auto_load_google_calendars(values)
        telegram_login_state = get_telegram_login_state()
        
        # Re-check Telegram status after login
        is_telegram_authed, telegram_user = get_telegram_user_info()
        if is_telegram_authed:
            telegram_info = f"✓ Logged in as {telegram_user}"
        else:
            telegram_info = "Telegram not logged in. Click Sign in with Telegram."
    else:
        channels, info = try_auto_load_channels(values)
        google_calendars, google_info = try_auto_load_google_calendars(values)

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
