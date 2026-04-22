import os
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template_string, request
from telethon.sync import TelegramClient

APP = Flask(__name__)
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WEBHOOK = "http://n8n:5678/webhook/telegram"

HTML = """
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Veloce Setup Wizard</title>
    <style>
      :root {
        --bg: #f4f7fb;
        --card: #ffffff;
        --ink: #14213d;
        --muted: #54627a;
        --accent: #e76f51;
        --accent-2: #2a9d8f;
      }
      body {
        margin: 0;
        font-family: \"Segoe UI\", Tahoma, sans-serif;
        background: radial-gradient(circle at 15% 0%, #dbe6f9 0%, var(--bg) 50%);
        color: var(--ink);
      }
      .shell {
        max-width: 880px;
        margin: 32px auto;
        background: var(--card);
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 18px 44px rgba(20, 33, 61, 0.12);
      }
      h1 { margin-top: 0; font-size: 1.9rem; }
      p { color: var(--muted); }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 16px;
      }
      label {
        display: block;
        font-weight: 600;
        margin-bottom: 6px;
      }
      input, textarea {
        width: 100%;
        box-sizing: border-box;
        border: 1px solid #ccd4e1;
        border-radius: 10px;
        padding: 10px 12px;
        font-size: 0.98rem;
      }
      textarea { min-height: 78px; resize: vertical; }
      .toggle {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 12px;
      }
      .toggle input { width: auto; }
      .actions { margin-top: 18px; }
      button {
        border: 0;
        border-radius: 10px;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        color: #fff;
        font-weight: 700;
        padding: 11px 16px;
        cursor: pointer;
      }
      .note {
        margin-top: 16px;
        border-left: 4px solid var(--accent-2);
        background: #eef8f6;
        padding: 10px 12px;
        border-radius: 8px;
        color: #18443d;
      }
      .ok {
        margin-bottom: 14px;
        border-left: 4px solid #2a9d8f;
        background: #edf9f7;
        padding: 10px 12px;
        border-radius: 8px;
      }
      .warn {
        margin-bottom: 14px;
        border-left: 4px solid #e76f51;
        background: #fff2ef;
        padding: 10px 12px;
        border-radius: 8px;
      }
      .channels {
        margin-top: 16px;
        border: 1px solid #d9e2ee;
        border-radius: 10px;
        padding: 12px;
        background: #fafcff;
      }
      .channel-tools {
        display: grid;
        grid-template-columns: 1fr 180px;
        gap: 8px;
        margin: 10px 0;
      }
      .channel-list {
        max-height: 260px;
        overflow-y: auto;
      }
      .channel-item {
        display: flex;
        gap: 8px;
        align-items: flex-start;
        margin-bottom: 8px;
        font-weight: 500;
      }
      .channel-item input[type="checkbox"] {
        width: 16px;
        min-width: 16px;
        height: 16px;
        margin: 2px 0 0;
        padding: 0;
        flex: 0 0 auto;
      }
      .channel-item span {
        display: inline-block;
        line-height: 1.35;
        word-break: break-word;
      }
      .channel-meta {
        display: block;
        margin-top: 2px;
      }
      .muted {
        color: var(--muted);
        font-size: 0.92rem;
      }
      .services {
        margin-top: 16px;
        border: 1px solid #d9e2ee;
        border-radius: 10px;
        padding: 12px;
        background: #fafcff;
      }
      .service-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid #e6edf6;
        padding: 8px 0;
        gap: 12px;
      }
      .service-row:last-child {
        border-bottom: 0;
      }
      .badge {
        display: inline-block;
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 0.83rem;
        font-weight: 700;
      }
      .badge-running {
        background: #def7ef;
        color: #116149;
      }
      .badge-stopped {
        background: #fdeceb;
        color: #8d231a;
      }
      .badge-unknown {
        background: #edf1f9;
        color: #394b63;
      }
      @media (max-width: 680px) {
        .channel-tools {
          grid-template-columns: 1fr;
        }
        .service-row {
          flex-direction: column;
          align-items: flex-start;
        }
      }
    </style>
    <script>
      function applySelectedChannels() {
        const selected = Array.from(document.querySelectorAll('input[name="selected_channels"]:checked')).map(el => el.value);
        const target = document.querySelector('textarea[name="telegram_channels"]');
        if (!target) return;

        const existing = target.value
          .split(',')
          .map(s => s.trim())
          .filter(Boolean);

        const merged = Array.from(new Set(existing.concat(selected)));
        target.value = merged.join(',');
      }

      function filterAndSortChannels() {
        const container = document.getElementById('channelList');
        if (!container) return;

        const searchText = (document.getElementById('channelSearch')?.value || '').trim().toLowerCase();
        const sortBy = document.getElementById('channelSort')?.value || 'latest';
        const items = Array.from(container.querySelectorAll('.channel-item'));

        items.forEach(item => {
          const haystack = (item.dataset.search || '').toLowerCase();
          item.style.display = haystack.includes(searchText) ? 'flex' : 'none';
        });

        const visibleItems = items.filter(item => item.style.display !== 'none');
        visibleItems.sort((a, b) => {
          if (sortBy === 'name') {
            return (a.dataset.label || '').localeCompare(b.dataset.label || '');
          }

          const aLast = Number(a.dataset.last || '0');
          const bLast = Number(b.dataset.last || '0');
          if (aLast !== bLast) {
            return bLast - aLast;
          }
          return (a.dataset.label || '').localeCompare(b.dataset.label || '');
        });

        visibleItems.forEach(item => container.appendChild(item));
      }

      document.addEventListener('DOMContentLoaded', function () {
        const searchInput = document.getElementById('channelSearch');
        const sortSelect = document.getElementById('channelSort');
        if (searchInput) searchInput.addEventListener('input', filterAndSortChannels);
        if (sortSelect) sortSelect.addEventListener('change', filterAndSortChannels);
        filterAndSortChannels();
      });
    </script>
  </head>
  <body>
    <div class=\"shell\">
      <h1>Veloce Local Setup</h1>
      <p>Configure Telegram, webhooks, and channel filtering without editing files manually.</p>

      {% if success %}
        <div class=\"ok\">{{ success }}</div>
      {% endif %}
      {% if error %}
        <div class=\"warn\">{{ error }}</div>
      {% endif %}

      <form method=\"post\">
        <div class=\"grid\">
          <div>
            <label>Telegram API ID</label>
            <input name=\"telegram_api_id\" required value=\"{{ values.telegram_api_id }}\" />
          </div>
          <div>
            <label>Telegram API Hash</label>
            <input name=\"telegram_api_hash\" required value=\"{{ values.telegram_api_hash }}\" />
          </div>
          <div>
            <label>n8n Webhook URL</label>
            <input name=\"n8n_webhook_url\" value=\"{{ values.n8n_webhook_url }}\" />
          </div>
          <div>
            <label>Timezone</label>
            <input name=\"generic_timezone\" value=\"{{ values.generic_timezone }}\" />
          </div>
          <div>
            <label>Google Client ID (optional)</label>
            <input name=\"google_client_id\" value=\"{{ values.google_client_id }}\" />
          </div>
          <div>
            <label>Google Client Secret (optional)</label>
            <input name=\"google_client_secret\" value=\"{{ values.google_client_secret }}\" />
          </div>
        </div>

        <div style=\"margin-top: 16px;\">
          <label>Listen only to these Telegram channels/chats</label>
          <textarea name=\"telegram_channels\" placeholder=\"Examples: @course_updates, -1001234567890\">{{ values.telegram_channels }}</textarea>
          <p>Use comma-separated values. You can mix usernames and numeric chat IDs.</p>
        </div>

        <div style=\"margin-top: 10px;\">
          <label>Keywords to detect relevant messages (optional)</label>
          <input name=\"listener_keywords\" placeholder=\"Leave empty to process all messages\" value=\"{{ values.listener_keywords }}\" />
        </div>

        <label class=\"toggle\"><input type=\"checkbox\" name=\"enable_google_sync\" {% if values.enable_google_sync %}checked{% endif %} /> Enable Google integration</label>
        <label class=\"toggle\"><input type=\"checkbox\" name=\"start_docker\" {% if values.start_docker %}checked{% endif %} /> Start Docker services after save</label>
        <label class=\"toggle\"><input type=\"checkbox\" name=\"telegram_auth\" {% if values.telegram_auth %}checked{% endif %} /> Run Telegram login now</label>

        <div class=\"actions\">
          <button type=\"submit\" name=\"action\" value=\"save\">Save configuration</button>
          <button type=\"submit\" name=\"action\" value=\"save_restart\" style=\"margin-left: 8px; background: #264653;\">Save and restart services</button>
          <button type=\"submit\" name=\"action\" value=\"list_channels\" style=\"margin-left: 8px;\">List my channels</button>
          <button type=\"button\" onclick=\"applySelectedChannels()\" style=\"margin-left: 8px; background: #1b4965;\">Use selected channels</button>
        </div>

        <div class=\"services\">
          <strong>Docker service status</strong>
          <p class=\"muted\" style=\"margin: 6px 0 8px;\">{{ services_summary }}</p>
          {% if service_statuses and service_statuses|length > 0 %}
            {% for svc in service_statuses %}
              <div class=\"service-row\">
                <span>{{ svc.name }}</span>
                {% if svc.state == 'running' %}
                  <span class=\"badge badge-running\">running</span>
                {% elif svc.state == 'stopped' %}
                  <span class=\"badge badge-stopped\">stopped</span>
                {% else %}
                  <span class=\"badge badge-unknown\">unknown</span>
                {% endif %}
              </div>
            {% endfor %}
          {% else %}
            <p class=\"muted\">No compose services were detected yet.</p>
          {% endif %}

          <div class=\"actions\" style=\"margin-top: 10px;\">
            <button type=\"submit\" name=\"action\" value=\"services_refresh\" style=\"background: #3d5a80;\">Refresh status</button>
            <button type=\"submit\" name=\"action\" value=\"services_start\" style=\"margin-left: 8px;\">Start services</button>
            <button type=\"submit\" name=\"action\" value=\"services_stop\" style=\"margin-left: 8px; background: #9b2226;\">Stop services</button>
            <button type=\"submit\" name=\"action\" value=\"services_restart\" style=\"margin-left: 8px; background: #2a9d8f;\">Restart services</button>
          </div>
        </div>

        <div class=\"channels\">
          <strong>Available channels/chats</strong>
          {% if channels and channels|length > 0 %}
            <p class=\"muted\">Select channels/chats, then click \"Use selected channels\" to fill the filter box.</p>
            <div class=\"channel-tools\">
              <input id=\"channelSearch\" type=\"text\" placeholder=\"Search channels...\" />
              <select id=\"channelSort\">
                <option value=\"latest\">Sort: Latest message</option>
                <option value=\"name\">Sort: Name</option>
              </select>
            </div>
            <div id=\"channelList\" class=\"channel-list\">
              {% for ch in channels %}
                <label class=\"channel-item\" data-search=\"{{ ch.search_text }}\" data-label=\"{{ ch.label|lower }}\" data-last=\"{{ ch.last_message_ts }}\">
                  <input type=\"checkbox\" name=\"selected_channels\" value=\"{{ ch.value }}\" />
                  <span>
                    {{ ch.label }}
                    <small class=\"muted channel-meta\">Last message: {{ ch.last_message_human }}</small>
                  </span>
                </label>
              {% endfor %}
            </div>
          {% else %}
            <p class=\"muted\">No channels loaded yet. Click \"List my channels\" after entering Telegram API ID/hash and completing Telegram login at least once.</p>
          {% endif %}
        </div>
      </form>

      <div class=\"note\">
        If Telegram login is enabled, continue in this terminal to enter your phone number and verification code.
      </div>
    </div>
  </body>
</html>
"""


def to_bool(value: str) -> str:
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


def default_values() -> dict:
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


def save_env(values: dict) -> None:
    lines = [
        f"TELEGRAM_API_ID={values['telegram_api_id']}",
        f"TELEGRAM_API_HASH={values['telegram_api_hash']}",
        f"N8N_WEBHOOK_URL={values['n8n_webhook_url']}",
        f"GENERIC_TIMEZONE={values['generic_timezone']}",
        f"GOOGLE_CLIENT_ID={values['google_client_id']}",
        f"GOOGLE_CLIENT_SECRET={values['google_client_secret']}",
        f"ENABLE_GOOGLE_SYNC={to_bool(values['enable_google_sync'])}",
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


def start_docker_stack() -> None:
  run_compose(["up", "-d"])


def stop_docker_stack() -> None:
  run_compose(["stop"])


def restart_docker_stack() -> None:
  # Recreate containers so updated env values are applied.
  run_compose(["up", "-d", "--force-recreate"])


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
                    channels = list_user_channels(values["telegram_api_id"], values["telegram_api_hash"])
                    success = f"Loaded {len(channels)} channels/chats from Telegram."
                elif action in {"save", "save_restart"}:
                    save_env(values)

                    if values["telegram_auth"]:
                        run_telegram_auth(values["telegram_api_id"], values["telegram_api_hash"])

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

    return render_template_string(
        HTML,
        values=values,
        channels=channels,
        success=success,
        error=error,
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
