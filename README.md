# TaskFlow

Private Telegram productivity app for two people: create tasks, rate each other's effort, complete work and track weekly points minus penalties. The Russian interface uses inline buttons and edits one menu message. All state persists in PostgreSQL, including unfinished forms.

## Features

- Peer rating, optional calendar deadlines and comments, five-item task lists.
- Backlog, todo, in progress, done and cancelled tasks; confirmed reopening.
- Editable penalty types with immutable calculation snapshots.
- Weekly results and history, personal targets and display timezones.
- Batched rating reminders with a persisted one-hour cooldown.
- Durable webhook inbox, notification outbox, score ledger and audit history.

Example menu: `🎯 НЕДЕЛЯ • Макс 7350 / 10000 • Вика 6900 / 10000`, followed by task, penalty, rating, results and settings buttons. `/start` and `/menu` always restore navigation.

## Architecture

`Telegram → FastAPI webhook → PostgreSQL inbox → aiogram handlers → services → repositories → PostgreSQL`. A worker processes updates transactionally and sends outbox notifications. There is no separate REST business API, JWT or Redis. See [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [product flows](docs/PRODUCT.md) and [open-source attribution](docs/OPEN_SOURCE.md).

## Local setup

Requirements: Python 3.12+, PostgreSQL 15+ or Docker, and a Telegram bot from the official @BotFather (`/newbot`). Both authorized users must open the bot and press Start before it can send them notifications.

```sh
make dev
cp .env.example .env
# Fill your local database URL, bot token and authorized IDs.
docker compose up -d db
make migrate
make run
```

The compose database uses `taskflow` / `local-development-only` on port 5432. These are disposable development credentials. Do not use production credentials locally. `REGISTER_WEBHOOK=false` prevents local startup from replacing the production webhook. Local UI delivery needs an HTTPS tunnel and its own bot; set `WEBHOOK_BASE_URL` and `REGISTER_WEBHOOK=true` only then. There is intentionally no polling mode.

## Environment

| Variable | Purpose / default |
| --- | --- |
| `BOT_TOKEN` | Secret BotFather token |
| `WEBHOOK_SECRET` | Secret random URL-safe 32–256 characters |
| `WEBHOOK_BASE_URL` | Public HTTPS service origin |
| `DATABASE_URL` | PostgreSQL URL; Supabase session pooler, port 5432, with TLS |
| `USER_MAX_ID`, `USER_VIKA_ID` | Distinct positive Telegram IDs, required |
| `USER_MAX_NAME`, `USER_VIKA_NAME` | Макс / Вика |
| `DEFAULT_TIMEZONE` | Shared scoring calendar, Europe/Helsinki |
| `DEFAULT_WEEKLY_TARGET` | Initial target, 10000 |
| `RATING_NOTIFICATION_COOLDOWN_MINUTES` | 60, cannot be lower |
| `RATING_NOTIFICATION_DEBOUNCE_SECONDS` | 5 |
| `ENVIRONMENT`, `LOG_LEVEL` | production / INFO |
| `REGISTER_WEBHOOK` | true; disable for local tests |
| `TEST_DATABASE_URL` | Dedicated disposable database ending in `_test` |

Use ordinary `postgresql://` URLs; passwords must be URL-encoded. `sslmode=require` enables TLS; prefer `sslmode=verify-full` when the provider certificate chain is trusted. Credentials never belong in Git. Per-user timezone settings affect displayed dates; both scores always use `DEFAULT_TIMEZONE` so comparisons share one week. Existing user targets/timezones and edited penalty defaults survive reseeding.

## Development and verification

```sh
make lint
make typecheck
make test
make check
make migration message="add task field"
make migrate
make docker
docker build --target test -t taskflow:test .
```

Integration tests are skipped without `TEST_DATABASE_URL`. They destroy only their dedicated `_test` database schema: never point them at production. See [testing](docs/TESTING.md).

Production Docker runs Alembic before Uvicorn as a non-root user. `docker compose up --build app` starts the app against the persistent local volume; inspect `/health` and `/ready` on port 10000. No application data is stored in that container.

## Production

[Deployment](docs/DEPLOYMENT.md) describes private GitHub → Supabase → Render setup. `render.yaml` defines a Docker Web Service on the user-selected Free plan with `/ready` health checks and deployment after CI checks. Startup creates/synchronizes users and seeds penalty types idempotently, then registers `/telegram/webhook` with Telegram. Supabase tables are created exclusively by Alembic.

## Troubleshooting

- `/health` 200 but `/ready` 503: inspect startup/migration/database errors.
- Bot silent: both users must press Start; inspect Telegram `getWebhookInfo`, Render logs and inbox failures.
- Supabase connection failures: use IPv4-compatible **session** pooler on 5432, not transaction pooler 6543; check TLS and encoded password.
- Stale button: reopen the list or `/menu`; old versions cannot overwrite newer data.
- Missed notification: delivery is at most one attempt, avoiding duplicate Telegram messages after ambiguous network errors. Pending ratings remain visible in the menu.

See [operations](docs/OPERATIONS.md) for recovery and rollback. Production verification evidence and remaining deployment gates are recorded in [readiness](docs/READINESS.md).
