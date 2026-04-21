import os
import threading
import webbrowser
from pathlib import Path

from flask import Flask, render_template_string, request
from telethon.sync import TelegramClient

APP = Flask(__name__)
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DEFAULT_WEBHOOK = "http://n8n:5678/webhook/telegram"

HTML = """
<!doctype html>
<html>
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
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
                font-family: "Segoe UI", Tahoma, sans-serif;
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
        </style>
    </head>
    <body>
        <div class="shell">
            <h1>Veloce Local Setup</h1>
            <p>Configure Telegram, webhooks, and channel filtering without editing files manually.</p>

            {% if success %}
                <div class="ok">{{ success }}</div>
            {% endif %}
            {% if error %}
                <div class="warn">{{ error }}</div>
            {% endif %}

            <form method="post">
                <div class="grid">
                    <div>
                        <label>Telegram API ID</label>
                        <input name="telegram_api_id" required value="{{ values.telegram_api_id }}" />
                    </div>
                    <div>
                        <label>Telegram API Hash</label>
                        <input name="telegram_api_hash" required value="{{ values.telegram_api_hash }}" />
                    </div>
                    <div>
                        <label>n8n Webhook URL</label>
                        <input name="n8n_webhook_url" value="{{ values.n8n_webhook_url }}" />
                    </div>
                    <div>
                        <label>Timezone</label>
                        <input name="generic_timezone" value="{{ values.generic_timezone }}" />
                    </div>
                    <div>
                        <label>Google Client ID (optional)</label>
                        <input name="google_client_id" value="{{ values.google_client_id }}" />
                    </div>
                    <div>
                        <label>Google Client Secret (optional)</label>
                        <input name="google_client_secret" value="{{ values.google_client_secret }}" />
                    </div>
                </div>

                <div style="margin-top: 16px;">
                    <label>Listen only to these Telegram channels/chats</label>
                    <textarea name="telegram_channels" placeholder="Examples: @course_updates, -1001234567890">{{ values.telegram_channels }}</textarea>
                    <p>Use comma-separated values. You can mix usernames and numeric chat IDs.</p>
                </div>

                <div style="margin-top: 10px;">
                    <label>Keywords to detect relevant messages</label>
                    <input name="listener_keywords" value="{{ values.listener_keywords }}" />
                </div>

                <label class="toggle"><input type="checkbox" name="enable_google_sync" {% if values.enable_google_sync %}checked{% endif %} /> Enable Google integration</label>
                <label class="toggle"><input type="checkbox" name="start_docker" {% if values.start_docker %}checked{% endif %} /> Start Docker services after save</label>
                <label class="toggle"><input type="checkbox" name="telegram_auth" {% if values.telegram_auth %}checked{% endif %} /> Run Telegram login now</label>

                <div class="actions">
                    <button type="submit">Save configuration</button>
                </div>
            </form>

            <div class="note">
                If Telegram login is enabled, continue in this terminal to enter your phone number and verification code.
            </div>
        </div>
    </body>
</html>
"""


def to_bool(value: str) -> str:
        return "true" if value else "false"


def default_values():
        return {
                "telegram_api_id": "",
                "telegram_api_hash": "",
                "n8n_webhook_url": DEFAULT_WEBHOOK,
                "generic_timezone": "Asia/Kuala_Lumpur",
                "google_client_id": "",
                "google_client_secret": "",
                "telegram_channels": "",
                "listener_keywords": "assignment,deadline,due,exam,project",
                "enable_google_sync": False,
                "start_docker": True,
                "telegram_auth": True,
        }


def save_env(values):
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


def run_telegram_auth(api_id: str, api_hash: str):
        print("\nAuthenticating Telegram session...")
        client = TelegramClient("veloce_session", api_id, api_hash)
        client.start()
        client.disconnect()
        print("Telegram authentication successful.")


def start_docker_stack():
        print("Starting Docker services...")
        os.system("docker compose up -d")


@APP.route("/", methods=["GET", "POST"])
def index():
        values = default_values()
        success = ""
        error = ""

        if request.method == "POST":
                values.update(
                        {
                                "telegram_api_id": request.form.get("telegram_api_id", "").strip(),
                                "telegram_api_hash": request.form.get("telegram_api_hash", "").strip(),
                                "n8n_webhook_url": request.form.get("n8n_webhook_url", DEFAULT_WEBHOOK).strip() or DEFAULT_WEBHOOK,
                                "generic_timezone": request.form.get("generic_timezone", "Asia/Kuala_Lumpur").strip() or "Asia/Kuala_Lumpur",
                                "google_client_id": request.form.get("google_client_id", "").strip(),
                                "google_client_secret": request.form.get("google_client_secret", "").strip(),
                                "telegram_channels": request.form.get("telegram_channels", "").strip(),
                                "listener_keywords": request.form.get("listener_keywords", "").strip() or "assignment,deadline,due,exam,project",
                                "enable_google_sync": bool(request.form.get("enable_google_sync")),
                                "start_docker": bool(request.form.get("start_docker")),
                                "telegram_auth": bool(request.form.get("telegram_auth")),
                        }
                )

                if not values["telegram_api_id"] or not values["telegram_api_hash"]:
                        error = "Telegram API ID and API Hash are required."
                else:
                        try:
                                save_env(values)

                                if values["telegram_auth"]:
                                        run_telegram_auth(values["telegram_api_id"], values["telegram_api_hash"])

                                if values["start_docker"]:
                                        start_docker_stack()

                                success = "Saved. Your .env is updated and selected setup steps have completed."
                        except Exception as exc:
                                error = f"Setup failed: {exc}"

        return render_template_string(HTML, values=values, success=success, error=error)


def launch_browser():
        webbrowser.open("http://127.0.0.1:8765")


if __name__ == "__main__":
        print("Opening Veloce setup wizard at http://127.0.0.1:8765")
        threading.Timer(1.0, launch_browser).start()
        APP.run(host="127.0.0.1", port=8765, debug=False)