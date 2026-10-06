# Testing

Run `make lint`, `make typecheck` and `make test`. Unit tests need no external services. Integration tests require a dedicated PostgreSQL database whose name ends in `_test`; set TEST_DATABASE_URL. The fixture drops its application schema and Alembic revision table before each test, migrates from empty and seeds real data. Never reuse production or a shared development database.

`docker build --target test -t taskflow:test .` starts PostgreSQL inside the isolated test build stage and runs the complete suite on Python 3.12. `make docker` builds the smaller non-root production image. GitHub Actions additionally exercises PostgreSQL 16 and dependency auditing.

## Coverage

- Pure rules: rating bounds/permissions, transition confirmations, all penalty methods, negative progress and exact cooldown boundary.
- Time: timezone-aware week boundaries, Monday alignment and daylight-saving 167/169-hour weeks.
- Repositories/services: empty migrations, peer creation/rating/completion, awarded and penalty snapshots, rate changes, reopen reversal, immutable event triggers, idempotent seed, concurrent completion/penalty creation and concurrent notification claims.
- Telegram: actual aiogram dispatch with mocked HTTP Bot API, durable inbox, one edited UI per user, duplicate and stale callbacks, optional fields, confirmations and unauthorized users.
- Mobile forms: back traversal, preserved drafts, minute/timezone/target presets, invalid input with retained buttons, stale confirmation rejection, optional effort edits/removal and populated-schema migration.
- HTTP: health/readiness, secret rejection, duplicate update IDs, invalid bodies and size limits.
- Recovery: unexpected handler rollback/retry with one safe notification; SQL error privacy; history timezone aggregation and bounded pagination.

There is no arbitrary percentage gate; required business flows and regression cases are the gate. Coverage reports show which UI branches need further tests. Mocked Telegram tests do not substitute for real production chat acceptance.

## Required production evidence

Health/readiness, valid registered webhook, real /start and task creation, peer rating, completion score, penalty snapshot/config change and confirmed reopening. Redeploy and verify persistent records. Record exact commands, counts and external outcomes in READINESS.md; skipped integration tests must never be reported as passed.

## Foundation gate

`make test-business-unit` runs isolated business services/rules with in-memory repository substitutes,
without Telegram or PostgreSQL. `TEST_DATABASE_URL=... make test-business` then runs real PostgreSQL
service/repository/migration checks; it fails when the database variable is missing. No mocked lock
behavior is treated as concurrency evidence. See [BUSINESS.md](BUSINESS.md) for transaction ownership.

The gate includes the full state/confirmation matrix, identity binding, rating bounds, award snapshots,
original-week reversals, all penalty methods, stale/tampered previews, configuration rollback, injected
mid-operation failures, shared-zone/DST boundaries and negative scores. PostgreSQL tests also assert
concurrent mutation winners, immutable snapshots/events, query indexes, bounded pagination, metadata
drift, clean migration startup and concurrent migrators. Revision 0003 downgrade/upgrade compares all
persisted business rows; destructive downgrades and invalid legacy upgrades must fail atomically.
