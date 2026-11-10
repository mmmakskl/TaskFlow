# Architecture

This document describes implemented code, not a proposed rewrite. Release verification and blockers
live in [QA_REPORT.md](QA_REPORT.md) and [READINESS.md](READINESS.md).

## Modules and dependencies

TaskFlow is one Python application with an HTTP server and two asynchronous worker loops.
The Telegram Mini App uses a small same-origin REST API authenticated with Telegram `initData`.
There is no JWT, Redis, scheduler service or polling process.

| Module | Responsibility |
| --- | --- |
| `app/main.py`, `app/startup.py` | App construction; production migration then Uvicorn entrypoint |
| `app/web/application.py`, `webhook.py`, `validation.py` | Lifespan, health/readiness, secret-verified durable ingestion, webhook setup |
| `app/web/miniapp.py`, `app/web/static/` | Telegram-authenticated Mini App API and bundled UI assets |
| `app/bot/dispatcher.py`, `handlers/`, `middlewares/` | aiogram routing, actor binding, form orchestration and stale-input guards |
| `app/bot/storage.py`, `render.py`, `keyboards/`, `states/`, `callbacks/` | PostgreSQL FSM, edited-message UI and callback contracts |
| `app/services/` | Authorization, validation, mutations, results and UI presentation |
| `app/repositories/` | Queries, bounded lists, row/advisory locks, outbox enqueue and audit inserts |
| `app/models/entities.py`, `app/schemas/penalties.py` | SQLAlchemy metadata and validated confirmation preview |
| `app/db/`, `alembic/` | Pool, idempotent seed, serialized migrations |
| `app/notifications/worker.py`, `delivery.py`, `messages.py` | Ordered update processing, outbox claims and Bot API delivery |
| `app/utils/`, `app/config/` | UTC/calendar/duration/text utilities, settings and log redaction |
| `tools/` | Local UX preview and production probe; not public endpoints |

Handlers orchestrate forms and call services; repositories own persistence. Mini App routes resolve
the signed Telegram sender to the seeded two-user allow-list, then call the same services as bot
handlers. They do not accept a client supplied actor identity. `UIService` builds compact screens but
does not own scoring mutations. Service methods accept a persisted authenticated actor,
revalidate its identity, and do not trust callback-supplied user identity.

## HTTP and Telegram update flow

1. Telegram POSTs to `/telegram/webhook`. Compare the secret header in constant time before ingestion.
2. Require ready workers, a body ≤65,536 bytes and a valid PostgreSQL-compatible Telegram update.
3. Insert `telegram_updates` with unique `update_id`; duplicate inserts do nothing. Commit before 200.
4. The update loop takes transaction advisory lock `78421310` across replicas and selects the oldest
   pending update. A not-yet-due head update delays later updates to preserve FSM order.
5. aiogram loads durable state, enforces the two-user/private-chat allow-list and dispatches a router.
   FSM reads do not allocate dialogs for unknown senders.
6. A single outer transaction includes business mutation, score/audit events, FSM and outbox enqueue.
   On success mark the inbox row done and erase its payload. Handler errors roll back before retry.

A failed update retries with bounded backoff, becoming failed after five attempts. The first failure
can enqueue one safe error notice for an authorized private-chat sender. Done updates older than
seven days are pruned. See [OPERATIONS.md](OPERATIONS.md) for recovery without bulk replay.

The Mini App is served from `/miniapp` and `/static/`. Its `/api/miniapp/*` routes require Telegram
Web App `initData` in the `Authorization: tma …` header. The backend verifies Telegram's HMAC
signature, freshness and sender allow-list before opening a database session. Mini App mutations
use the same transaction, domain services, idempotency keys and immutable score events as bot
updates. No schema migration is required.

## Transactions and concurrency

`@atomic` service methods open savepoints; the caller owns the outer commit. Outside handlers, use
`async with factory.begin() as session`. Never assume returning from a service has committed data.
`SELECT FOR UPDATE` reloads existing entities so cached ORM state cannot overwrite a concurrent edit.
Entity versions reject obsolete buttons/previews. Task/penalty creation also takes an operation-key
advisory lock and relies on unique operation keys. Score/outbox dedupe keys guard duplicate effects.

The update worker deliberately serializes this two-user workload across replicas. Notification
workers run independently, claim due rows with `SKIP LOCKED`, and serialize cooldown reservations
on the recipient using `FOR NO KEY UPDATE` (compatible with task-insert foreign-key locks).
PostgreSQL constraints and immutable-event/snapshot triggers provide a second line of defense.
No business data or FSM depends on a container filesystem.

## Scoring and calendar

Results aggregate signed `score_events`, not live task ratings. DONE copies `rated_points` into
`awarded_points` and appends a positive task event effective at `completed_at`. Reopen/cancel of DONE
appends an equal negative event effective at the original completion, then clears the current award.
A later completion posts a new award in its new completion week. Penalty creation posts negative
points effective at its creation; soft deletion posts an original-date positive compensation.

Week bounds are local Monday 00:00 to next Monday, converted to UTC, using the shared configurable
`DEFAULT_TIMEZONE` (Europe/Helsinki). They are half-open and DST-aware. Per-user timezone affects
screen dates/deadlines only. History uses the current shared timezone and current personal targets;
there are no historical target snapshots or manual Week records.

## Notification delivery

Task creation enqueues one pending rating batch per evaluator via a partial unique index, initially
due after the default five-second debounce. On claim, count current pending tasks and consult the
PostgreSQL clock plus `users.last_rating_notification_at`. Empty queues skip; cooldown delays the
batch until the next eligible time. Reservation commits before sending, at most one attempt per
60 minutes per user. The cooldown survives replicas, process restarts and database restarts.

Approval queues a proposal-author notice; rerating queues an executor notice; rejection queues a proposal-author notice; penalty creation queues a recipient notice. Other status changes
produce no routine notices. Explicit Telegram rate-limit rejections may retry; ambiguous network
failures become `uncertain` and are not replayed. Sending rows abandoned for five minutes also become
uncertain. This trades possible omission for avoiding duplicate spam, not exactly-once delivery.
See [NOTIFICATIONS.md](NOTIFICATIONS.md) for the complete state machine.

## UI and callback safety

FSM state, draft data, menu message ID and rendered screen live in `dialogs`. A new prompt changes
its flow generation; callbacks carry entity/version or form generation rather than trusted business
values. Confirmation checks the active FSM and stored intent. Navigation resets interrupted forms;
Back preserves valid draft fields. `/start` clears the old reply keyboard and opens the Mini App;
manually typed `/menu` and `/cancel` restore the legacy inline menu. The bot command menu and
notification action keyboards are removed.

Render edits text or markup, tolerates unchanged text and replaces deleted/uneditable menu messages.
Lists use five-item pagination; user HTML is escaped and button labels shortened. Safe DomainError
messages stay in the current flow. Unexpected errors are logged with IDs; raw DB/secret details do
not reach users. Telegram message edits are external effects and cannot participate in DB rollback;
a retry can repeat an edit, while transactional business mutations remain protected.

## Lifecycle and deployment

`python -m app.startup` validates configuration, migrates to head under session advisory lock
`78421309`, then execs Uvicorn as PID 1. Lifespan seeds users/types under a separate transaction lock,
registers URL + secret with Telegram and starts workers. Production rejects disabled registration.
Shutdown drains workers up to 25 seconds, closes resources, and leaves the webhook registered.

`/health` is liveness only. `/ready` requires initialized app, live worker task and a DB probe with a
five-second deadline. The DB pool is three connections plus two overflow per replica, with bounded
checkout/connect/command timeouts. Render hosts a Free Docker Web Service; Supabase supplies persistent
PostgreSQL. The first old-consumer upgrade requires the maintenance procedure in
[DEPLOYMENT.md](DEPLOYMENT.md). Auto-deploy configuration and actual release status are distinct.

## Schema and extension policy

Migration connections pin `search_path=public` so a login named `taskflow` cannot confuse schema
reflection. All application DDL remains explicitly schema-qualified. Current Alembic head is `0008`: initial schema, optional estimates, business guards, notification
claim/delivery diagnostics, collaborative task roles, fixed and volume-priced templates, template soft deletion and task hierarchy/links. See [DATA_MODEL.md](DATA_MODEL.md) for fields, indexes and rollback limits.
Add a new migration for schema changes; do not rewrite applied revisions or create provider UI tables.
Change scoring/access/idempotency only with business and real PostgreSQL regression tests.
Preserve the small MIT pagination attribution documented in [OPEN_SOURCE.md](OPEN_SOURCE.md).
