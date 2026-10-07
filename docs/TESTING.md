# Testing

## Safe environment setup

Use Python 3.12+ and a disposable real PostgreSQL database. Follow README first, then:

```sh
docker compose up -d db
# Once per new local volume; an existing database needs no recreation:
docker compose exec -T db createdb -U taskflow taskflow_test
export TEST_DATABASE_URL=postgresql://taskflow:local-development-only@localhost:5432/taskflow_test
make lint
make typecheck
make test
make check
```

The fixture reads `TEST_DATABASE_URL` from the **shell**, not `.env`. It drops `taskflow` and
`public.alembic_version`, migrates from empty and seeds before each DB-dependent test. The database
name must end in `_test`. Never run tests against production, a shared DB or the local application's
`taskflow` database. DB-dependent tests skip without the variable; report skips separately.
The DB role must own the disposable database for migration/search-path checks. Tests are sequential;
do not run parallel pytest processes against one database.

Unit tests use pytest/pytest-asyncio without external services. PostgreSQL tests use SQLAlchemy async
and real constraints/locks, with mocked Bot API transport where handlers are involved. A fake Telegram
token in tests does not verify live Telegram delivery. Business tests use `_env_file=None` settings.

## Commands and suites

| Command | Purpose |
| --- | --- |
| `make test-business-unit` | Isolated service/rule gate, no Telegram or PostgreSQL |
| `make test-business` | Unit gate + real business repository/service/migration checks; requires exported URL |
| `make test-ux` | Focused dispatcher, forms and navigation; requires exported URL |
| `make test` | Entire pytest suite; inspect skips |
| `venv/bin/python -m pytest -q tests/unit` | Unit-only run |
| `venv/bin/python -m pytest -q --cov=app --cov-report=term-missing` | Full suite and line coverage report |
| `make lint`, `make typecheck` | Ruff lint/format check and mypy application checks |
| `make check` | Lint, types and tests |
| `docker build --target test -t taskflow:test .` | Isolated Python 3.12/PostgreSQL 15 build-stage full gate |
| `make docker` | Non-root production image build; does not run tests |

GitHub Actions uses PostgreSQL 16, Python 3.12, lint, types, business unit gate, full suite, runtime
`pip-audit` and production Docker build. There is no configured minimum coverage percentage;
required invariants, flow coverage and regression tests are the gate. See [QA_REPORT.md](QA_REPORT.md)
for dated counts rather than assuming a historical count is current.

## What to test when changing behavior

- **Tasks/scoring:** owner/evaluator binding, rating bounds, all transitions, confirmed reopen,
  awarded snapshot, completion-week membership, negative/exact/over-target results.
- **Time:** shared scoring versus personal display timezone, Monday/end-exclusive bounds, DST,
  tasks created and completed in different weeks, original-week compensation.
- **Penalties/settings:** three methods, immutable snapshots, type edits during preview,
  soft-delete compensation, stale versions and duplicate confirmation.
- **Transactions:** concurrent completion/creation/claim winners, partial-failure rollback,
  stale identity-map refresh, operation keys and immutable-event triggers.
- **Notifications:** ten-task coalescing, recipient locks, 60-minute cooldown, persistent restart,
  empty queues, deferred batches, explicit retry_after and ambiguous send handling.
- **Telegram:** dispatcher/FSM with mocked transport, Back/skips/presets, interrupted draft,
  `/menu` mid-form, old buttons, unexpected text/media, missing menu and transport failures.
- **Web/security:** health/ready and DB hang, worker failure, wrong/non-ASCII secret, malformed,
  huge/deep JSON and out-of-range IDs, unauthorized/group access, escaped text and redacted errors.

Add a regression that fails for a reproduced bug, then verify the fix. Relevant focused suites include
`test_business_*`, `test_services.py`, `test_notifications.py`, `test_mobile_ux.py`,
`test_form_navigation.py`, `test_web_and_bot.py`, `test_production_lifecycle.py`,
`test_qa_adversarial.py` and `test_qa_inputs.py`.

## Migration gates

`test_business_migrations.py` and `test_business_foundation.py` cover empty/populated upgrade,
concurrent migrators, guards/indexes/metadata drift and safe 0003 roundtrip. Notification tests cover
0004 roundtrip and legacy in-flight behavior. Destructive 0001/0002 downgrades must refuse.
Run migration experiments only on disposable copies. Autogenerate a revision in development,
review SQL/triggers/backfill, then execute full PostgreSQL tests before deploy.

## Manual acceptance and production evidence

[UX_ACCEPTANCE.md](UX_ACCEPTANCE.md) gives the mobile checklist. Both real accounts must pass
create → peer rate → complete → reopen, penalty creation/config change/snapshot and stale-button
recovery. Inspect actual Telegram rendering and taps; a browser preview is not phone evidence.

[DEPLOYMENT.md](DEPLOYMENT.md) provides hardened container and live probes. Verify revision head,
health/readiness, secret rejection, actual Telegram update delivery and persistent data after a real
Render redeploy. Record exact commit, commands, counts and outcomes in [READINESS.md](READINESS.md).
Never describe mocked tests, skipped integration or an old deployment as live acceptance.
