# Data model

All application tables are in the private `taskflow` PostgreSQL schema. Schema creation, constraints, indexes and immutable-event triggers are versioned in Alembic.

| Table | Responsibilities |
| --- | --- |
| users | Unique Telegram identity, role, name, timezone, target, rating cooldown |
| tasks | Owner/evaluator, text, deadline, status, rating, awarded snapshot, lifecycle timestamps, version and operation key |
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
