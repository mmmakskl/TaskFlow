# Architecture

## Modules and update flow

FastAPI validates the constant-time webhook secret before parsing a bounded 64 KiB request. It inserts the update ID into the durable inbox and acknowledges only after commit. Duplicate IDs return success without another row.

A background worker reads inbox rows in Telegram update order. A PostgreSQL transaction advisory lock serializes the two-user application's state changes and FSM across service replicas. aiogram access middleware checks the ENV allow-list and private sender/chat identity. Handlers render screens and translate input; services enforce rules and repositories issue bound ORM queries.

FSM state and menu message IDs live in `dialogs`, not memory or Redis. A redeploy can resume an unfinished form. Callbacks carry record versions or random form generations; expired buttons produce safe feedback. Entity mutations use row locks and expected versions. Creation operations additionally use unique operation keys and transaction advisory locks.

## Scoring and history

`score_events` is an append-only signed ledger. Completing a rated task records its awarded snapshot at completion time. Reopening adds an equal negative event effective in the original completion week. Completing it again records the new completion week. Penalty creation adds a negative snapshot amount; confirmed soft deletion reverses it in its original week. Database triggers reject UPDATE/DELETE of score and audit events.

Weeks are local Monday midnight through the next Monday, converted to UTC half-open intervals. Calendar arithmetic handles daylight saving changes. Shared scoring timezone comes from ENV; user timezone is only a display preference. Changing the shared timezone deliberately reinterprets historical week boundaries. Targets are current preferences, not historical target snapshots.

## Notifications

Task creation queues one pending rating batch per recipient with a five-second debounce. A worker locks the recipient, recounts pending tasks and claims at most one send attempt in 60 minutes. It commits cooldown and `sending` status before contacting Telegram. Rated-task messages have unique deduplication keys. Unknown network outcomes are not retried: Telegram sendMessage lacks an idempotency key. This favors no spam over guaranteed notification delivery. The menu remains authoritative.

## Failure and lifecycle

Unexpected handler exceptions roll back business writes and schedule bounded inbox retries; a safe error notice is queued once. Five failures become a dead letter. Logs identify update/action IDs and redact secrets. Shutdown waits for workers, closes the bot session and disposes database connections without unsetting a webhook registered by a newer replica.

## Deployment boundaries

Docker runs migration and web server, with PostgreSQL storing every durable state. Migrations hold a session advisory lock across Alembic commits, so use a direct or session-pooled connection. The `taskflow` schema is private; Supabase Data API is disabled and autogenerate only examines this schema. No Redis, JWT, public business API, filesystem database or long polling is used.
