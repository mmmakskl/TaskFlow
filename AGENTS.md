# Repository Guidelines

## Architecture and Ownership

TaskFlow: private tasks/points bot for Max and Vika.
Python 3.12+, aiogram 3, FastAPI, async SQLAlchemy, PostgreSQL, Alembic.
`webhook → durable inbox → routers → services → repositories → PostgreSQL`;
an outbox loop delivers notifications.

`app/bot/` owns routers, keyboards, callback/FSM guards and rendering;
`app/services/` owns business rules; `app/repositories/` owns queries/locks;
`app/models/`, `app/schemas/`: entities/validation; `app/db/`, `alembic/`: pool/seed/DDL; `app/web/` owns HTTP/lifecycle; `app/notifications/` owns delivery.
`tests/{unit,integration}/`: tests; `docs/`: runbooks.

## Business Invariants

- Authenticate the ENV-configured Telegram sender in their matching private chat. Exactly two users;
  tasks may be self-assigned, peer-assigned or shared; creator and executor are distinct.
  Other proposal author approves; template self/shared tasks are pre-rated.
- Manual tasks create BACKLOG; approval → TODO; template self/shared create TODO; TODO → IN_PROGRESS/DONE; IN_PROGRESS → TODO/DONE.
  Cancellation requires confirmation; CANCELLED is terminal. Leaving DONE requires confirmation.
- Shared completion awards full price to both atomically; reopening reverses both original awards.
- Only current manual rater may change TODO/IN_PROGRESS price, confirmed; never DONE/CANCELLED.
- Title/effort changes reset approval; deadline/comment do not. Executor controls accepted personal tasks.
- Completion stores `awarded_points`; count by `completed_at`, never task creation/rating date.
  Sum immutable score events in shared `DEFAULT_TIMEZONE` Monday-to-Monday bounds.
- Reopen reverses the original award in its original week; penalty deletion compensates its original
  week. Insert score/audit events, never rewrite history. CANCELLED contributes no award.
- Penalties retain immutable calculation snapshots; reject changed previews.
- Mutations: transactions, row/version locks, operation keys; caller commits.
- Rating batches debounce; PostgreSQL cooldown permits at most one attempt/hour/user across restarts.
  Never replay ambiguous sends.

## Commands, Migrations and Tests

`make dev`, `make migrate`, `make run`; `make lint`, `make typecheck`, `make test`, `make check`.
Export disposable `TEST_DATABASE_URL` ending `_test`; tests destroy its schema. Skips are not passes.
Focused gates: `make test-business-unit`, `make test-business`, `make test-ux`.
`make docker`; `docker build --target test -t taskflow:test .` runs isolated PostgreSQL tests.

Schema: reviewed revision,
`make migration message="description"`, empty/populated upgrades, rollback analysis.
Never edit applied revisions. Invariant changes require regression tests. Use typed four-space
Python, Ruff (100 columns), mypy, pytest-asyncio; conventional commits, PR test/migration evidence.

## UX and Production

UX: edited menu, inline buttons, Back/skips/presets, five-item pages, confirmations.
`/menu` interrupts forms; reject stale buttons, escape text.
Render Free Docker Web Service uses external Supabase PostgreSQL, verified-secret webhook,
`/health` and `/ready`. Startup locks migrations, seeds/registers webhook; shutdown drains workers.
Read `docs/{DEPLOYMENT,OPERATIONS,QA_REPORT}.md` before deploying.
CI gates lint/types/tests/dependencies/Docker; verify live delivery/persistence.

## DO NOT

- Put business logic in handlers.
- Use SQLite in production or depend on local files for production data/FSM.
- Recalculate historical penalties from current `penalty_types`.
- Derive completed score from current `rated_points`.
- Bypass Alembic.
- Use long polling in production.
- Commit secrets or log credentials/private payloads.
- Remove idempotency protections.
- Send one rating notification per task.
