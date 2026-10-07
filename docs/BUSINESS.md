# Business layer

## Boundaries and transaction ownership

Telegram middleware resolves an authenticated Telegram identity. Services independently reload that
identity and verify its database ID and ENV allow-list membership. Executors are derived from a typed SELF/PEER/SHARED choice, never arbitrary form IDs.
The approver is the other configured participant relative to the current proposal author. Additional database users do not
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

- Ordinary tasks are BACKLOG until approved; unchanged template SELF/SHARED tasks are TODO.
  Template PEER tasks still require acceptance. Approval itself earns zero points.
- Creator, executor(s), proposal author and current rating author are separate roles. Before first
  acceptance creator controls the offer; afterwards only the personal executor, or either shared
  participant, controls work. Both configured users may view all tasks.
- Title/effort changes reset approval and propose the previous price to the other editor;
  deadline/comment do not. First acceptance is retained. Cancelled tasks are terminal.
- Only the current manual rater may rerate TODO/IN_PROGRESS after explicit confirmation. Status
  remains unchanged. No DONE/CANCELLED rerating, no retrospective score changes.
- Completion snapshots the price in awarded_points. A shared snapshot is per person and inserts
  two user-specific score events in one locked transaction, with one completed_at.
- Reopen inserts negative snapshots at the original completed_at for every beneficiary.
  Recompletion inserts fresh awards in its new completion week; history is never overwritten.
- Results sum immutable ledger events within half-open Monday-to-Monday DEFAULT_TIMEZONE bounds.
  User timezones affect display/deadlines only. DST weeks may have 167 or 169 hours.
- User task summaries cover all pages/filters: TODO and IN_PROGRESS planned points, pending count,
  and all-time category=task ledger sum without penalties. Shared work counts once per person.
- Template data is snapshotted; later template edits never rewrite a task's terms. Creation locks
  and checks the selected template version/active status; mismatches require explicit resubmission.

## Penalties and concurrency

Preview and confirmation are separate. Confirmation reloads and locks the type, checks its version
and recomputes points. Full name/description/method/rate/quantity/points snapshots are stored with
creation. Type changes never recalculate existing penalties; PostgreSQL rejects snapshot updates.
Deletion is soft and inserts a compensating event in the original penalty week.

Creation operation keys are required and unique. Transaction advisory locks serialize duplicate
creates; repeats return the original record only to the original creator, independently of task executor. Row locks plus strict version
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
