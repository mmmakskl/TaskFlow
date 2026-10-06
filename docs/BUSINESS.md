# Business layer

## Boundaries and transaction ownership

Telegram middleware resolves an authenticated Telegram identity. Services independently reload that
identity and verify its database ID and ENV allow-list membership. Task owners cannot be supplied as
form fields; the evaluator is the other configured participant. Additional database users do not
become authorized participants.

Handlers delegate to services. Repositories own SQL, pagination, row locks, advisory locks and outbox
queries. Services own validation, transitions, snapshots and audit/event creation. No service commits
an outer transaction. Use one `AsyncSession` per request/job and an explicit unit of work:

```python
async with sessions.begin() as session:
    service = TaskService(TaskRepository(session), settings)
    actor = await service.user_repo.user(authenticated_telegram_id)
    task = await service.create(actor, "Book tickets", operation_key)
```

Each mutation enters a savepoint. Exceptions roll back that operation, including its audit, score and
notification writes, even when a caller catches the exception and commits other work. SQLAlchemy
flushes existing pending work before entering a savepoint: callers must not place unvalidated ORM
changes in the session. Refresh previously modified ORM objects after a failed operation before
reading their attributes; rollback may expire them. An outer rollback discards every operation.

## Task and scoring invariants

- New tasks are `BACKLOG`, unrated and worth zero earned points.
- Only the evaluator may rate, using an integer 1–10000; rating transitions to `TODO`.
- Only the owner changes status. Completing `TODO`/`IN_PROGRESS` copies the rating to
  `awarded_points` and sets `completed_at` in the same transaction.
- A task score event uses `completed_at`, independent of task creation or later rating changes.
- Confirmed reopening/cancellation inserts the negative award at its **original** effective timestamp
  and clears completion fields. Recompletion inserts a fresh award in the new completion week.
- Results aggregate immutable signed events: task awards minus non-deleted penalties, including
  compensating events. History is recomputed, never stored as editable weekly totals.
- The shared competition timezone is `DEFAULT_TIMEZONE`; user timezones control personal date
  presentation. Weeks are local Monday midnight to the next Monday, with an exclusive upper bound.
  DST weeks may contain 167 or 169 hours. Negative results remain negative.

## Penalties and concurrency

Preview and confirmation are separate. Confirmation reloads and locks the type, checks its version
and recomputes points. Full name/description/method/rate/quantity/points snapshots are stored with
creation. Type changes never recalculate existing penalties; PostgreSQL rejects snapshot updates.
Deletion is soft and inserts a compensating event in the original penalty week.

Creation operation keys are required and unique. Transaction advisory locks serialize duplicate
creates; repeats return the original record only to the original actor. Row locks plus strict version
checks serialize rating, completion, reopening, deletion and configuration. Stale repeats fail safely
without another score/audit/notification mutation. Outbox writes share the business transaction.
Rating batches have a partial unique index; a locked persistent user cooldown permits at most one
attempt per hour. Telegram delivery itself occurs after commit.

## Schema and query patterns

Alembic alone owns the `taskflow` schema. Revision `0003` validates existing rows before installing
stronger constraints; inconsistent legacy rows abort the whole migration without rewriting data.
Partial indexes serve active owner lists, pending evaluator lists and non-deleted penalty pagination.
Ordered penalty types and a covering date/user score index support settings and weekly/history
aggregation. See [DATA_MODEL.md](DATA_MODEL.md) for all tables.

`0003 → 0002 → 0003` preserves tasks, estimates, penalties, score events and audit events. It only
removes/reinstalls the new guards/indexes. Application rollback should normally retain the current
schema. Revisions `0002` and `0001` deliberately refuse destructive downgrades; use a reviewed forward
migration or restore a verified backup instead.

## Independent acceptance gate

```sh
make test-business-unit
TEST_DATABASE_URL=postgresql://postgres@localhost/taskflow_test make test-business
make lint typecheck
```

Unit tests substitute repositories without Telegram, SQL or simulated locks. PostgreSQL tests prove
actual locking, atomic failures, constraints, snapshot/event immutability, bounded queries, clean
migrations, concurrent migration startup and safe downgrade/upgrade behavior. Use a disposable
`_test` database only: fixtures destroy and recreate its application schema. Full regression tests
remain necessary when connecting handlers to these services.
