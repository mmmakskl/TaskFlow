# Data model

All ten application tables are in the `taskflow` PostgreSQL schema. The Alembic revision table is
`public.alembic_version`. Migrations own schema, constraints, indexes and immutable-event triggers.
Current head: **0005**. Timestamps use PostgreSQL `timestamptz` (aware UTC in Python); JSON is JSONB.
Schema separation is not a permission boundary: do not expose it through a public Data API.

| Table | Responsibilities |
| --- | --- |
| users | Unique Telegram identity, role, name, timezone, target, rating cooldown |
| tasks | Owner/evaluator, text, deadline, optional estimated_minutes, status, rating, awarded snapshot, lifecycle timestamps, version and operation key |
| task_templates | Shared title, effort, fixed points, active flag, creator, version, operation and seed keys |
| penalty_types | Live name/emoji/description, PER_MINUTE/FIXED/MANUAL calculation, rate, active flag, sort order, version and unique seed key |
| penalties | Target/creator/type FKs, full type snapshots, quantity, points, comment, version, operation key and soft deletion |
| audit_events | Actor, entity/action, old/new JSONB and timestamp; immutable |
| score_events | User, category, signed points, effective timestamp and unique operation key; immutable |
| dialogs | Durable aiogram FSM state and JSONB data per bot/chat/user key |
| telegram_updates | Unique update ID, durable payload, attempts, due time and status |
| notifications | Recipient, kind, snapshot payload, unique dedupe key, status/due time, attempts, claim/finish timestamps and bounded error code |

Creator, personal executor, proposal author and approver are distinct roles. The approver must differ
from proposal author; a peer-assigned executor may approve their own assigned task. Manual rated_by
must equal approver; automatic TEMPLATE rating has no manual rater. Rated points are 1–10000. DONE requires completion time and awarded snapshot; other states have neither. The service layer enforces the allowed transition graph and actor permissions. Foreign keys retain referenced records; task cancellation and penalty deletion are non-destructive.

Indexes support owner/status pagination, pending evaluator tasks, completion dates, ledger date/user aggregation, due notifications and ordered inbox processing. A partial unique index permits only one pending rating batch per recipient. Version checks prevent stale configuration and entity callbacks from overwriting newer changes.

## Migration workflow

1. Change models and write a failing regression test.
2. `make migration message="describe change"` against a development database.
3. Review generated SQL, defaults, backfill and locking. Migration reflection pins `search_path=public`,
   independently of the login name or database default search path. Autogenerate is restricted to `taskflow` so provider-owned schemas are untouched.
4. Run migrations from an empty `_test` database and upgrade a populated development database.
5. Deploy additive/backward-compatible changes first; introduce breaking changes in stages.

The initial downgrade deliberately refuses to drop all production data. Immutable event triggers must never be disabled during normal operations. Reversals are inserts, not edits. Supabase UI must not create or alter application tables.

## Task estimates (revision 0002)

`estimated_minutes` is nullable or an integer 1–10080. It represents planned effort and never changes scoring. Existing tasks remain null after the additive migration. Active-task owner edits use row locks/version checks and include the estimate in audit snapshots. Application rollback can safely ignore the retained column; its destructive downgrade is refused.

## Business guards (revision 0003)

For manual ratings, fields are either all null or all populated. Revision 0005 adds explicit TEMPLATE
provenance and requires a template snapshot for automatic ratings. BACKLOG is unrated; TODO/IN_PROGRESS/DONE are
rated. CANCELLED requires its cancellation timestamp; only DONE retains completion/award fields.
Versions are positive. Penalty calculation checks enforce minute × rate, fixed rate and manual
quantity shapes; multiplication uses bigint to avoid overflow. A trigger prevents alteration of
penalty snapshots, ownership, operation key, comment and creation timestamp while allowing soft
deletion/version updates.

Partial indexes cover active owner and pending evaluator pagination and non-deleted penalties.
`ix_penalty_types_order` serves ordered settings lists. `ix_score_effective_user` covers date-range
aggregation/history with user, category and points; the existing user/date index remains for
user-specific queries. Revision 0003 installs guards transactionally and validates old rows. Its
downgrade removes only these guards/indexes and preserves all records.


## Ledger and relationships

`tasks.owner_user_id`, `evaluator_user_id` and `rated_by_user_id` reference users. `penalties`
references target, creator and type; notifications and score/audit events reference users.
Users have unique Telegram IDs and roles (`max`, `vika`); startup requires exactly two records.
Changing the configured Telegram ID for an existing role preserves that role's business data.

Results sum signed `score_events` within `[start, end)` by `effective_at`. DONE posts `+awarded_points`
at `completed_at`; reopen posts the negative snapshot at that same timestamp. Penalty creation posts
negative points at `created_at`; deletion posts a positive compensation at that same timestamp.
The event ledger and audit log reject UPDATE/DELETE through PostgreSQL triggers. Current entity rows
can change; historical events remain append-only. No manual Week table or target-history table exists.

## Delivery and FSM state

`telegram_updates.update_id` is a BIGINT primary key. Status is pending/done/failed; successful
processing clears private payloads, then completed rows are eligible for seven-day pruning.
`dialogs` uses a bot/chat/user key with state and JSONB draft/navigation data. Reads do not create rows.
The application serializes update transactions across replicas to protect FSM transitions.

Notifications use a unique dedupe key, pending/sending/sent/skipped/failed/uncertain status, due time
and snapshot payload. A partial unique index allows only one pending `rating_pending` batch per
recipient. `users.last_rating_notification_at` records reserved attempts, not proven delivery.

## Notification diagnostics (revision 0004)

Adds `claimed_at`, `finished_at`, `attempts` and bounded `error_code`, plus
`ix_notifications_sending_claimed` for abandoned claims. Legacy sending rows become uncertain;
no migration replays them. Downgrade retains business rows, statuses and cooldown but removes these
diagnostics. Pause consumers and back up first; prefer a forward fix.

## Index map and review checklist

| Query | Index / protection |
| --- | --- |
| Owner's active list / status filter | Partial `ix_tasks_active_owner_id`, `ix_tasks_owner_status_id` |
| Evaluator's pending queue | Partial `ix_tasks_pending_evaluator_id`, `ix_tasks_evaluator_status_id` |
| Completion dates | `ix_tasks_completed_at` |
| User/week totals and multi-user history | `ix_score_user_effective`, covering `ix_score_effective_user` |
| Live penalty list / recipient chronology | `ix_penalties_live_id`, `ix_penalties_target_created` |
| Ordered type settings | `ix_penalty_types_order` |
| Due notifications / abandoned sends | `ix_notifications_due`, `ix_notifications_sending_claimed` |
| Ordered pending inbox / audit lookup | `ix_updates_pending`, `ix_audit_entity` |
| Duplicate creates/events/batches | Unique operation/dedupe keys and `uq_rating_pending` |

Metadata autogeneration cannot fully describe custom triggers/backfills. Review revision SQL and
update tests for constraints, populated upgrades, rollback safety and real concurrent operations.
See [BUSINESS.md](BUSINESS.md), [TESTING.md](TESTING.md) and [OPERATIONS.md](OPERATIONS.md).

## Collaborative tasks (revision 0005)

Adds task creator, PERSONAL/SHARED kind, optional second participant, proposal author/points, first
acceptance, rating source, and template ID/version/snapshot. The two participant columns expose a
synchronous participant_user_ids tuple; exactly two configured users make a separate membership
table unnecessary. Creator and participant identity and original template snapshot are immutable.
First acceptance cannot be cleared after reapproval. Legacy tasks become PERSONAL with creator=owner
and existing manual rating provenance; score and audit records remain unchanged.

Shared completion posts a separate immutable event for each participant, keyed by task/user/cycle.
The single awarded_points is the price per participant, not their combined reward. A shared reopen
compensates each event at the original completion time.

New task_templates records store shared fixed-price presets with versioned edits and soft disable.
Snapshot/version and automatic-price provenance distinguish generated tasks from manual approval.

Revision 0005 refuses destructive downgrade. Once collaborative data exists, old application code
is incompatible: do not run old and new workers together; prefer forward fixes.
