# Data model

All application tables are in the private `taskflow` PostgreSQL schema. Schema creation, constraints, indexes and immutable-event triggers are versioned in Alembic.

| Table | Responsibilities |
| --- | --- |
| users | Unique Telegram identity, role, name, timezone, target, rating cooldown |
| tasks | Owner/evaluator, text, deadline, optional estimated_minutes, status, rating, awarded snapshot, lifecycle timestamps, version and operation key |
| penalty_types | Live name/emoji/description, PER_MINUTE/FIXED/MANUAL calculation, rate, active flag, sort order, version and unique seed key |
| penalties | Target/creator/type FKs, full type snapshots, quantity, points, comment, version, operation key and soft deletion |
| audit_events | Actor, entity/action, old/new JSONB and timestamp; immutable |
| score_events | User, category, signed points, effective timestamp and unique operation key; immutable |
| dialogs | Durable aiogram FSM state and JSONB data per bot/chat/user key |
| telegram_updates | Unique update ID, durable payload, attempts, due time and status |
| notifications | Recipient, kind, payload, unique dedupe key, delivery status and due time |

Task owner and evaluator must differ; rated_by must equal evaluator. Rated points are 1–10000. DONE requires completion time and awarded snapshot; other states have neither. The service layer enforces the allowed transition graph and actor permissions. Foreign keys retain referenced records; task cancellation and penalty deletion are non-destructive.

Indexes support owner/status pagination, pending evaluator tasks, completion dates, ledger date/user aggregation, due notifications and ordered inbox processing. A partial unique index permits only one pending rating batch per recipient. Version checks prevent stale configuration and entity callbacks from overwriting newer changes.

## Migration workflow

1. Change models and write a failing regression test.
2. `make migration message="describe change"` against a development database.
3. Review generated SQL, defaults, backfill and locking. Autogenerate is restricted to `taskflow` so provider-owned schemas are untouched.
4. Run migrations from an empty `_test` database and upgrade a populated development database.
5. Deploy additive/backward-compatible changes first; introduce breaking changes in stages.

The initial downgrade deliberately refuses to drop all production data. Immutable event triggers must never be disabled during normal operations. Reversals are inserts, not edits. Supabase UI must not create or alter application tables.

## Task estimates (revision 0002)

`estimated_minutes` is nullable or an integer 1–10080. It represents planned effort and never changes scoring. Existing tasks remain null after the additive migration. Active-task owner edits use row locks/version checks and include the estimate in audit snapshots. Application rollback can safely ignore the retained column; its destructive downgrade is refused.

## Business guards (revision 0003)

Rating fields are either all null or all populated. BACKLOG is unrated; TODO/IN_PROGRESS/DONE are
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
