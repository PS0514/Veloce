import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request
from telethon.sync import TelegramClient

APP = Flask(__name__)
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WEBHOOK = "http://n8n:5678/webhook/telegram"


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


def default_values() -> dict[str, str | bool]:
    env_map = read_env_file()
    return {
        "telegram_api_id": env_map.get("TELEGRAM_API_ID", ""),
        "telegram_api_hash": env_map.get("TELEGRAM_API_HASH", ""),
        "n8n_webhook_url": env_map.get("N8N_WEBHOOK_URL", DEFAULT_WEBHOOK),
        "generic_timezone": env_map.get("GENERIC_TIMEZONE", "Asia/Kuala_Lumpur"),
        "google_client_id": env_map.get("GOOGLE_CLIENT_ID", ""),
        "google_client_secret": env_map.get("GOOGLE_CLIENT_SECRET", ""),
        "telegram_channels": env_map.get("TELEGRAM_CHANNEL_FILTERS", ""),
        "listener_keywords": env_map.get("LISTENER_KEYWORDS", ""),
        "enable_google_sync": env_map.get("ENABLE_GOOGLE_SYNC", "false").lower() == "true",
        "start_docker": True,
        "telegram_auth": True,
    }


def save_env(values: dict[str, str | bool]) -> None:
    lines = [
        f"TELEGRAM_API_ID={values['telegram_api_id']}",
        f"TELEGRAM_API_HASH={values['telegram_api_hash']}",
        f"N8N_WEBHOOK_URL={values['n8n_webhook_url']}",
        f"GENERIC_TIMEZONE={values['generic_timezone']}",
        f"GOOGLE_CLIENT_ID={values['google_client_id']}",
        f"GOOGLE_CLIENT_SECRET={values['google_client_secret']}",
        f"ENABLE_GOOGLE_SYNC={to_bool(bool(values['enable_google_sync']))}",
        f"TELEGRAM_CHANNEL_FILTERS={values['telegram_channels']}",
        f"LISTENER_KEYWORDS={values['listener_keywords']}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_telegram_auth(api_id: str, api_hash: str) -> None:
    print("\nAuthenticating Telegram session...")
    client = TelegramClient("veloce_session", api_id, api_hash)
    client.start()
    client.disconnect()
    print("Telegram authentication successful.")


def list_user_channels(api_id: str, api_hash: str) -> list[dict[str, str]]:
    channels: list[dict[str, str]] = []
    client = TelegramClient("veloce_session", api_id, api_hash)
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


def compose_base_command() -> list[str]:
    deploy_compose = ROOT / "deploy" / "docker-compose.yaml"
    if deploy_compose.exists():
        return ["docker", "compose", "-f", str(deploy_compose)]
    return ["docker", "compose"]


def run_compose(args: list[str]) -> str:
    command = compose_base_command() + args
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output or "Docker compose command failed.")
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
    success = ""
    error = ""
    info = ""
    service_statuses, services_summary = get_services_status()

    if request.method == "POST":
        action = request.form.get("action", "save")
        values.update(
            {
                "telegram_api_id": request.form.get("telegram_api_id", "").strip(),
                "telegram_api_hash": request.form.get("telegram_api_hash", "").strip(),
                "n8n_webhook_url": request.form.get("n8n_webhook_url", DEFAULT_WEBHOOK).strip() or DEFAULT_WEBHOOK,
                "generic_timezone": request.form.get("generic_timezone", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur",
                "google_client_id": request.form.get("google_client_id", "").strip(),
                "google_client_secret": request.form.get("google_client_secret", "").strip(),
                "telegram_channels": request.form.get("telegram_channels", "").strip(),
                "listener_keywords": request.form.get("listener_keywords", "").strip(),
                "enable_google_sync": bool(request.form.get("enable_google_sync")),
                "start_docker": bool(request.form.get("start_docker")),
                "telegram_auth": bool(request.form.get("telegram_auth")),
            }
        )

        actions_requiring_telegram = {"save", "save_restart", "list_channels"}
        needs_telegram = action in actions_requiring_telegram

        if needs_telegram and (not values["telegram_api_id"] or not values["telegram_api_hash"]):
            error = "Telegram API ID and API Hash are required."
        else:
            try:
                if action == "list_channels":
                    channels = list_user_channels(str(values["telegram_api_id"]), str(values["telegram_api_hash"]))
                    success = f"Loaded {len(channels)} channels/chats from Telegram."
                elif action in {"save", "save_restart"}:
                    save_env(values)

                    if values["telegram_auth"]:
                        run_telegram_auth(str(values["telegram_api_id"]), str(values["telegram_api_hash"]))

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

        service_statuses, services_summary = get_services_status()

        if action != "list_channels":
            channels, info = try_auto_load_channels(values)
    else:
        channels, info = try_auto_load_channels(values)

    return render_template(
        "setup_wizard.html",
        values=values,
        channels=channels,
        success=success,
        error=error,
        info=info,
        service_statuses=service_statuses,
        services_summary=services_summary,
    )


def launch_browser() -> None:
    webbrowser.open("http://127.0.0.1:8765")


def run_setup_wizard() -> None:
    print("Opening Veloce setup wizard at http://127.0.0.1:8765")
    threading.Timer(1.0, launch_browser).start()
    APP.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    run_setup_wizard()
