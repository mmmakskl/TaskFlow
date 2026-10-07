# Notification subsystem

## Producers and transactions

Task creation requiring approval inserts a `rating_pending` outbox batch in the same transaction as the task.
The partial unique index permits one pending batch per recipient. The first event sets a bounded
debounce (`RATING_NOTIFICATION_DEBOUNCE_SECONDS`, default 5); subsequent events do not extend it.
Each approval or rerating inserts a version-keyed `rated` event. Approval goes to the current
proposal author; rerating goes to the executor (shared: the other participant) and snapshots the
old/new prices and actual status. Rejection inserts `task_rejected` for the proposal author. Confirmed penalty creation inserts one `penalty_created`
event per penalty, including self-assigned penalties. Payloads snapshot names and points. Unique
operation keys, row versions, transactions and outbox dedupe keys prevent duplicate side effects.
Status transitions, reopen and configuration edits do not enqueue notifications.

## Claims, batching and cooldown

The notification loop runs independently of the Telegram update loop inside the web process.
Each replica selects one due row with `FOR UPDATE SKIP LOCKED`, then locks the recipient using
`FOR NO KEY UPDATE`. This serializes claims without conflicting with task foreign-key locks.
All scheduling/cooldown decisions use PostgreSQL's clock, read again after acquiring locks.

For rating batches, count currently visible BACKLOG tasks at claim time. An empty queue is skipped.
Otherwise reserve `users.last_rating_notification_at`, mark the row `sending` and commit before
calling Telegram. A batch inside cooldown remains pending until that timestamp plus the configured
cooldown (minimum 60 minutes). It is sent only if tasks still await rating. A delivered batch does not
schedule recurring reminders by itself; another task creation is needed to queue another batch.
Pending batches and cooldown survive process, Render and database connection-pool restarts.

## Delivery guarantees and errors

At most one **rating send attempt** per recipient per cooldown is reserved. Names/titles are HTML
escaped; each message includes a button into the existing UI. The notification API timeout is 20s.

Telegram `sendMessage` has no client idempotency key. Exactly-once delivery across PostgreSQL and
Telegram cannot be guaranteed: a crash between claim commit and delivery can omit a message.
Ambiguous network errors become `uncertain` and are never automatically replayed. Interrupted
`sending` rows older than five minutes become `uncertain`; the reserved cooldown stays intact.
Users can always inspect pending tasks from the dashboard.

An explicit Telegram `retry_after` rejection is safe to defer. A rating retry coalesces into the
pending batch and still obeys the full cooldown. Other notification kinds retry at the indicated
time up to three attempts. API rejections (including blocked bots), malformed payloads and unknown
kinds terminate without replay. Both users must first start the bot.

Logs contain notification ID, internal recipient ID, kind, status and a bounded error code; they
exclude notification text, API exception bodies and credentials. PostgreSQL rows also record
attempt count, claim/finish timestamps and error code. A send whose outcome cannot be recorded
remains `sending` until expiry; it must not be bulk replayed.

## Migration and validation

Alembic 0004 adds delivery diagnostics and an index for in-flight expiry. Existing `sending` rows
become `uncertain` without replay. Downgrade to 0003 drops only diagnostic columns/index; business
records, outbox statuses and user cooldown remain. Prefer a forward fix; pause workers and back up
before any downgrade, since diagnostic timestamps would be lost.

For the first rollout from a version without `penalty_created` support, drain/stop the old worker
before enabling the new application. Do not overlap old consumers with new outbox producers:
the old delivery code does not understand the new event kind. Later rollouts with compatible
consumers can share the queue through the normal database locks.

Run `pytest tests/unit/test_business_notifications.py` and, with a disposable `TEST_DATABASE_URL`,
`pytest tests/integration/test_notifications.py`. PostgreSQL tests cover batching, concurrent
producers/consumers, FK lock interactions, duplicate actions, cooldown after restart, rollback,
empty queues, delivery errors, rate limiting, migration roundtrip and independent worker loops.

## Collaborative revision compatibility

Revision 0005 preserves the 0004 delivery model and cooldown. Automatic self/shared template tasks
need no approval batch; peer-assigned templates do. Semantic task edits enqueue a new approval batch
without replaying ambiguous sends. A repeated price save is a no-op. Routine shared completion and
reopen remain silent. After 0005, destructive schema downgrade is refused; the 0004 rollback details
above describe a database still on that historical revision.
