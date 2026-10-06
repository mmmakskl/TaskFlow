# Repository Guidelines

## Product and Architecture

TaskFlow is a private Telegram task and points app for Max and Vika. Use Python 3.12+, aiogram 3, FastAPI, SQLAlchemy async, PostgreSQL and Alembic. Telegram handlers call services; services call repositories. Keep business rules out of handlers. Production uses a secret-verified webhook, never polling.

## Project Structure

`app/bot/` contains handlers, keyboards, callbacks, middleware and persistent FSM storage. `app/services/` owns business rules; `app/repositories/` owns queries. Models live in `app/models/`, database setup in `app/db/`, HTTP endpoints in `app/web/`, and durable workers in `app/notifications/`. Use `tests/unit/`, `tests/integration/`, `alembic/versions/` and `docs/` for their respective concerns.

## Commands and Style

Run `make dev`, copy `.env.example` to `.env`, then `make migrate` and `make run`. `make test` runs pytest; integration requires a disposable `TEST_DATABASE_URL` ending in `_test`. `make lint`, `make typecheck` and `make check` are quality gates. `make docker` builds production; `docker build --target test -t taskflow:test .` runs PostgreSQL tests inside Docker.

Use four spaces, typed Python functions, snake_case names and PascalCase classes. Ruff formats to 100 columns; mypy checks application code. Tests use `test_*.py` and pytest-asyncio.

## Invariants

- Only the two ENV-configured Telegram accounts have access; the opposite user rates a task.
- Count completed points by `completed_at`, never `created_at`; `awarded_points` is a snapshot.
- Existing penalties retain snapshots when their types change.
- Reopen and deletion append compensating score events and audit events; never mutate history.
- Rating notifications allow at most one attempt per hour per user, with PostgreSQL cooldown and locking.
- Duplicate callbacks must not duplicate mutations, awards or penalties.
- Schema changes require Alembic migrations and regression tests: `make migration message="description"`, review SQL, then `make migrate`.
- Production data and FSM never depend on local files.

## Security, UX and Delivery

Keep credentials in ENV and Render secrets; redact logs. Validate callbacks, escape user text and preserve authorization. Prefer one edited menu, inline buttons, five-item pagination, optional-field skips and destructive confirmations.

Use descriptive conventional commits (`feat:`, `fix:`, `docs:`). PRs explain changes, tests, migrations and UX examples. CI checks lint, types, PostgreSQL tests, dependencies and Docker. Render deploys the Docker web service after checks pass; migrations acquire a session advisory lock. Never change scoring, access, snapshots, cooldown or migration rules without tests. See `docs/OPERATIONS.md` before deployment or rollback.
