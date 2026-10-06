# Testing

Run `make lint`, `make typecheck` and `make test`. Unit tests need no external services. Integration tests require a dedicated PostgreSQL database whose name ends in `_test`; set TEST_DATABASE_URL. The fixture drops its application schema and Alembic revision table before each test, migrates from empty and seeds real data. Never reuse production or a shared development database.

`docker build --target test -t taskflow:test .` starts PostgreSQL inside the isolated test build stage and runs the complete suite on Python 3.12. `make docker` builds the smaller non-root production image. GitHub Actions additionally exercises PostgreSQL 16 and dependency auditing.

## Coverage

- Pure rules: rating bounds/permissions, transition confirmations, all penalty methods, negative progress and exact cooldown boundary.
- Time: timezone-aware week boundaries, Monday alignment and daylight-saving 167/169-hour weeks.
- Repositories/services: empty migrations, peer creation/rating/completion, awarded and penalty snapshots, rate changes, reopen reversal, immutable event triggers, idempotent seed, concurrent completion/penalty creation and concurrent notification claims.
- Telegram: actual aiogram dispatch with mocked HTTP Bot API, durable inbox, one edited UI per user, duplicate and stale callbacks, optional fields, confirmations and unauthorized users.
- HTTP: health/readiness, secret rejection, duplicate update IDs, invalid bodies and size limits.

There is no arbitrary percentage gate; required business flows and regression cases are the gate. Coverage reports show which UI branches need further tests. Mocked Telegram tests do not substitute for real production chat acceptance.

## Required production evidence

Health/readiness, valid registered webhook, real /start and task creation, peer rating, completion score, penalty snapshot/config change and confirmed reopening. Redeploy and verify persistent records. Record exact commands, counts and external outcomes in READINESS.md; skipped integration tests must never be reported as passed.
