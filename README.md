# TaskFlow

Private Telegram productivity app for Max and Vika: create a task, have the other person rate it,
complete it, and track weekly points minus penalties. The Russian interface uses inline buttons,
compact screens, a calendar and five-item lists. Task estimates, deadlines and comments are optional.
Forms and all business data persist in PostgreSQL.

**Release status:** the audited application is deployed on Render with schema 0004. Health,
readiness, signed webhook processing and migration persistence pass. Real two-account Telegram
acceptance remains deferred. See [fresh review](docs/PRODUCTION_REVIEW.md),
[QA report](docs/QA_REPORT.md) and [release evidence](docs/READINESS.md).

## Architecture and repository map

`Telegram → FastAPI webhook → PostgreSQL inbox → aiogram → services → repositories → PostgreSQL`.
A separate outbox loop sends notifications. No REST business API, JWT, Redis or polling.

| Directory | Ownership |
| --- | --- |
| `app/bot/` | Routers, keyboards, callbacks, persistent FSM and message rendering |
| `app/services/` | Permissions, task transitions, scoring mutations and settings |
| `app/repositories/` | Bounded queries, locks and persistence |
| `app/models/`, `app/schemas/` | SQLAlchemy entities and validated penalty previews |
| `app/db/`, `alembic/` | Connections, seed and versioned DDL |
| `app/web/`, `app/notifications/` | HTTP ingestion/lifecycle and durable workers/delivery |
| `tests/`, `tools/`, `docs/` | Regression suites, deployment/UX tools and handoff guides |

Start with [PRODUCT](docs/PRODUCT.md), [ARCHITECTURE](docs/ARCHITECTURE.md) and
[DATA_MODEL](docs/DATA_MODEL.md). Contributor rules are in [AGENTS.md](AGENTS.md).

## First local launch

Requirements: Python 3.12+, `make`, Docker Engine/Desktop with Compose, Git and a separate development
bot created with [@BotFather](https://t.me/BotFather). Run all commands from the repository root.
Do not use the production bot token or database for local work. Port 5432 must be available.

```sh
git clone git@github.com:mmmakskl/TaskFlow.git
cd TaskFlow
python3 --version                 # Must be 3.12 or newer
make dev                         # Creates venv and installs pinned dev dependencies
cp .env.example .env             # Fresh checkout only; do not overwrite an existing .env
venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Edit `.env`: enter the development `BOT_TOKEN`, put the generated value in `WEBHOOK_SECRET`, and
set two distinct `USER_MAX_ID` / `USER_VIKA_ID` values to the actual Telegram numeric IDs.
Keep `ENVIRONMENT=development`, `REGISTER_WEBHOOK=false` and the example local `DATABASE_URL`.
The example token is a placeholder, not a usable bot token.

```sh
docker compose up -d db
docker compose exec -T db pg_isready -U taskflow -d taskflow
make migrate                     # Empty database → current Alembic head (0004)
venv/bin/python -m alembic current
make run                         # Terminal 1: HTTP server at http://localhost:8000
```

In terminal 2:

```sh
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl -i -X POST -H 'Content-Type: application/json' \
  --data '{"update_id":1}' http://127.0.0.1:8000/telegram/webhook
```

Expect `{"status":"ok"}`, `{"status":"ready"}` and **403** for the unsigned POST.
Application startup seeds the two users and seven default penalty types. `make migrate` alone only
creates schema. Stop `make run` with Ctrl-C. `make start` combines migration and local server startup.

### Receive real Telegram updates locally

`REGISTER_WEBHOOK=false` starts HTTP/workers but does not route Telegram to localhost.
Install [cloudflared](https://developers.cloudflare.com/tunnel/downloads/), then expose port 8000
with a [development Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/):

```sh
cloudflared tunnel --url http://localhost:8000
```

Set `WEBHOOK_BASE_URL` to the printed HTTPS **origin** (no path) and `REGISTER_WEBHOOK=true` in `.env`;
restart `make run`. Open the development bot from both allowed accounts and press Start.
If the tunnel logs QUIC/UDP timeouts, stop it and use
`cloudflared tunnel --protocol http2 --url http://localhost:8000` instead.
The app registers `<origin>/telegram/webhook` with the secret automatically. A changed tunnel URL
requires another restart. Use a separate bot: Telegram supports one webhook per bot, so reusing the
production token redirects its updates. See [webhook operations](docs/OPERATIONS.md).

## Tests and quality gates

Create the disposable integration database **once** in the local Compose PostgreSQL instance:

```sh
docker compose exec -T db createdb -U taskflow taskflow_test
export TEST_DATABASE_URL=postgresql://taskflow:local-development-only@localhost:5432/taskflow_test
make lint
make typecheck
make test-business-unit
make test                        # Full suite, including real PostgreSQL
make check                       # lint + typecheck + tests
```

`TEST_DATABASE_URL` must be exported in the shell; the test fixture does not load it from `.env`.
Tests drop/recreate `taskflow` and the Alembic version table. Never target a shared or production DB;
the name must end in `_test`. Without the variable, DB-dependent tests **skip**. Use
`make test-business` and `make test-ux` for focused gates; both require that variable.
[TESTING.md](docs/TESTING.md) explains coverage, migration tests and manual acceptance.

## Docker

Stop the native server before starting another worker against the same development DB.

```sh
make docker                      # Production image taskflow:latest
# Uses .env, but overrides DATABASE_URL to reach the Compose db service:
docker compose up -d --build app
curl --fail http://127.0.0.1:10000/health
curl --fail http://127.0.0.1:10000/ready
docker compose logs --tail=50 app
docker compose stop app
# Self-contained lint/types/dependency audit + PostgreSQL tests (takes several minutes):
docker build --target test -t taskflow:test .
```

The production image runs as non-root, migrates before Uvicorn and uses external PostgreSQL.
The Compose database persists in its named volume; `docker compose down` retains it.
Do not use `docker compose down -v` unless deliberately deleting disposable local data.

## Configuration

| Variable | Purpose / default |
| --- | --- |
| `BOT_TOKEN` | Secret BotFather token; required |
| `WEBHOOK_SECRET` | Required URL-safe secret, 32–256 characters |
| `WEBHOOK_BASE_URL` | Public HTTPS origin; required when registering webhook |
| `DATABASE_URL` | Required PostgreSQL URL; example uses local Compose credentials |
| `USER_MAX_ID`, `USER_VIKA_ID` | Required, distinct positive Telegram IDs |
| `USER_MAX_NAME`, `USER_VIKA_NAME` | Макс / Вика; startup synchronizes configured names |
| `DEFAULT_TIMEZONE` | Shared scoring week, Europe/Helsinki |
| `DEFAULT_WEEKLY_TARGET` | Initial personal target, 10000 |
| `RATING_NOTIFICATION_COOLDOWN_MINUTES` | 60 minimum |
| `RATING_NOTIFICATION_DEBOUNCE_SECONDS` | 5, allowed 1–60 |
| `ENVIRONMENT`, `LOG_LEVEL` | Set development locally, production / INFO on Render |
| `REGISTER_WEBHOOK` | false in example; must be true in production |
| `PORT` | Container startup port, defaults to 10000; `make run` uses 8000 |
| `TEST_DATABASE_URL` | Exported disposable `_test` DB URL, tests only |

Ordinary `postgresql://` URLs work; URL-encode passwords. For Supabase use the session pooler on
5432 with TLS, not transaction pooler 6543. Personal timezone changes display/deadline dates;
weekly comparisons use the shared `DEFAULT_TIMEZONE`. Reseeding preserves personal targets/timezones
and edited penalty defaults. Credentials belong in local ignored ENV files or Render secrets.

## Deploy to Render

Follow [DEPLOYMENT.md](docs/DEPLOYMENT.md) for the complete private GitHub → persistent Supabase →
Render Docker **Web Service** procedure, environment values, first-upgrade maintenance window and
live persistence probes. `render.yaml` targets Frankfurt Free; cold-start latency is an accepted
tradeoff. The Blueprint requests deploy after CI passes; the existing service is temporarily Off
until its incompatible consumer upgrade is completed. Schema changes use Alembic exclusively.

## Troubleshooting and further reading

- `/health` 200, `/ready` 503: inspect migration, worker and PostgreSQL connectivity errors.
- Bot silent: confirm the development tunnel/webhook, both users' Start, and Telegram getWebhookInfo.
- DB tests skipped: export `TEST_DATABASE_URL`; creating `.env` alone does not enable them.
- Stale button: reopen the list or `/menu`. It cannot overwrite newer entity versions.
- Missing notification: an ambiguous send is not replayed; pending ratings remain in the menu.

See [OPERATIONS](docs/OPERATIONS.md), [NOTIFICATIONS](docs/NOTIFICATIONS.md),
[UX](docs/UX.md), [manual UX acceptance](docs/UX_ACCEPTANCE.md) and
[open-source attribution/licenses](docs/OPEN_SOURCE.md).
