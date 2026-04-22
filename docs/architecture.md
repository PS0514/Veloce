# Veloce Architecture

## Goals

- Keep onboarding separate from runtime services.
- Keep local runtime state out of version control.
- Make integrations modular so Telegram, n8n, and Google can evolve independently.

## Recommended Layout

```
src/
	veloce/
		config.py
		listener_service.py
		setup_wizard.py
scripts/
	run_setup.py
	run_listener.py
deploy/
	docker-compose.yaml
	listener.Dockerfile
docs/
glm/
```

## Runtime Paths

- `setup.py` is a backward-compatible entrypoint that delegates to `src/veloce/setup_wizard.py`.
- `listener.py` is a backward-compatible entrypoint that delegates to `src/veloce/listener_service.py`.
- New scripts in `scripts/` are preferred for future automation and CI.
- Docker runtime is centralized in `deploy/docker-compose.yaml` and `deploy/listener.Dockerfile`.

## Data Flow

1. User runs setup wizard (`python setup.py` or `python scripts/run_setup.py`).
2. Wizard writes configuration to `.env` and optionally performs Telegram auth.
3. Listener runs (`python listener.py` or `python scripts/run_listener.py`).
4. Listener filters messages by `TELEGRAM_CHANNEL_FILTERS` and `LISTENER_KEYWORDS`.
5. Matching messages are forwarded to `N8N_WEBHOOK_URL`.

## Configuration Keys

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_CHANNEL_FILTERS`
- `LISTENER_KEYWORDS`
- `N8N_WEBHOOK_URL`
- `GENERIC_TIMEZONE`
- `ENABLE_GOOGLE_SYNC`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Use `.env.example` as the template and keep `.env` local only.
