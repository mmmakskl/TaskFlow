# Independent QA / SRE release audit

Date: 2026-10-07. Target: the `production-deployment` release and the fixes described below.

## Release decision

**NOT PRODUCTION READY / rollout gate remains closed.** The local release gates pass, but the new
release has not been deployed to Render. Browser automation returns `cgWindowNotFound`; new-release
Render logs, real Telegram delivery, and persistence across an actual Render redeploy remain
unverified. The older production service is still running. Auto-deploy is temporarily Off for the
first incompatible notification-consumer upgrade; follow [DEPLOYMENT.md](DEPLOYMENT.md) before
restoring After CI Checks Pass. Do not treat old-release health checks as new-release acceptance.

No unresolved high-severity defect was identified in the audited application paths. This is a
bounded audit, not proof that every possible defect has been eliminated.

## Method and environment

Reviewed handlers, FSM storage/guards, service transactions, repositories, SQL constraints/triggers,
indexes, scoring/timezone functions, outbox claims/delivery, webhook parsing, logging and deployment.
Existing assertions were examined, not used as the sole oracle. Added independent hostile-input,
SQL reconciliation, week-boundary and transport-failure tests. Tests first reproduced failures before
fixes. Business reconciliation compares current task/penalty rows against ledger totals directly.

- Python 3.12, real disposable PostgreSQL 15 and 17; never SQLite.
- aiogram dispatcher/FSM and durable inbox exercised with a mocked Telegram transport.
- HTTP tests use FastAPI lifespan and real PostgreSQL, plus injected unavailability/hangs.
- Final code tested from a frozen Git index export. Pre-existing unstaged callback/documentation
  edits and `tests/unit/test_callbacks.py` were excluded; they are not part of this audit's release.
- Production inspected read-only. Destructive tests, migration downgrade/upgrade and DB interruption
  ran only against disposable `_test` databases. Production business records were not altered.

## Findings and fixes

| ID | Severity | Reproduction / impact | Fix and regression |
| --- | --- | --- | --- |
| QA-001 | Medium | Five unknown senders were denied but created five durable FSM dialogs because aiogram reads state before the access middleware. Enables unnecessary persistent allocation. No private data was disclosed. | FSM reads no longer insert; only writes allocate dialogs. Unknown IDs and group-chat spoof tests assert zero unwanted dialogs. |
| QA-002 | Medium | NUL and lone Unicode surrogates passed business validation, then failed PostgreSQL text/JSON serialization. A bad input could enter the inbox retry path. | Shared PostgreSQL text validation rejects them with a safe DomainError before mutation. Title/operation-key regressions and real transaction recovery checks. |
| QA-003 | Medium | Task IDs above PostgreSQL INTEGER range caused DB binding errors rather than a stale-action response. | Bound repository entity IDs and rating cursors to the storage range, independently of callback parsing. Real PostgreSQL overflow regressions. |
| QA-004 | Medium | Authenticated malformed updates with oversized update IDs, NUL, lone surrogates or NaN passed model parsing and failed inbox insertion with server errors. | Validate JSONB-compatible strings/numbers and signed BIGINT update IDs before opening the inbox transaction; catch excessive JSON nesting. Each regression expects 400 and no inbox row. |
| QA-005 | Medium | A hanging DB operation left `/ready` waiting on driver/pool defaults. | Five-second readiness deadline, five-second pool checkout and ten-second connection timeout. Injected hang returns 503; `/health` stays independent. |
| QA-006 | Low | Deleting a task while custom rating was open incorrectly told the evaluator that they were rating their own task. | Deleted-task rating now raises StaleAction. Handler regression verifies an obsolete-task warning, no rating and no score event. |

Initial independent run: **13 failures**, all reproduced against real PostgreSQL or direct business
validation. A separate failing deleted-rating test established QA-006. All now pass. No database
schema change was needed for these fixes.

## Scenario matrix

“Pass” below means automated/isolated verification, not live two-account phone acceptance.

| Area | Tested scenarios | Result / evidence |
| --- | --- | --- |
| AUTH | Max, Vika, unknown sender, mismatched persisted identity, spoofed group/chat, cross-user mutations | Pass; middleware and service authorization, new adversarial auth tests. |
| TASK CREATE | Title only, deadline/calendar, comment, estimate, 200-character limit, blank/oversized/invalid text, cancellation halfway | Pass; mobile/form tests plus hostile Unicode transaction regressions. Owner/evaluator and BACKLOG verified in DB. |
| RATING | Peer only, owner denied, custom/preset values, zero/negative/huge values, duplicate/stale callbacks, deleted task during open custom form | Pass; permissions, version locking and deleted-task regression. No duplicate notification/award. |
| STATUS | TODO → IN_PROGRESS/DONE, IN_PROGRESS → DONE, repeated DONE, confirmed reopen, cancel, cancelled award attempt | Pass; transition matrix, real concurrent mutations, mixed-action SQL reconciliation. Reopen reverses the original week's award. |
| WEEKLY SCORE | Monday inclusive, Sunday exclusive end, DST/timezone, creation/completion in different weeks, negative/exact/over-target results | Pass; independent local-calendar oracle for Helsinki, Moscow, New York and Kiritimati; SQL completion-week tests; progress/result tests. |
| PENALTIES | Self/other recipient, PER_MINUTE/FIXED/MANUAL, zero/negative/huge inputs, rate snapshots, disabled type, type edits during FSM, duplicate confirmation | Pass; immutable snapshot constraints, preview version checks, live dispatcher configuration-race test and duplicate-operation tests. |
| NOTIFICATIONS | Ten-task batch, cooldown boundary, concurrent producers/consumers, restart persistence, ambiguous network errors and explicit retry_after | Pass; PostgreSQL locking/unique-index tests and actual isolated DB/container restart below. At-most-once ambiguous attempts remain a documented tradeoff. |
| TELEGRAM | Old buttons, deleted/uneditable target, `/menu` during forms, unexpected text/media, expired ack and network failures | Pass with mocked Bot API; single-message recovery tests. Injected DONE render failure rolls back, then retries to exactly one 700-point award/audit. |
| DATABASE | Empty → head, concurrent migrations, safe enforcement downgrade/upgrade, destructive downgrade refusal, constraints/triggers/index plans, stale identity map, partial failure rollback | Pass against PostgreSQL. Lock refresh prevents a cached task overwriting a newer rating. Production backup restore was separately verified before this audit. |
| WEB | Health, ready with unavailable/hanging DB, failed worker/startup, wrong/missing/non-ASCII secret, malformed/oversized update, duplicate update | Pass; HTTP/lifespan and adversarial ingestion tests. Secret is verified before accepting/storing updates. |
| SECURITY | Callback tampering/foreign identity, parameterized SQL, HTML escaping, DB exception detail redaction, known production credentials in Git | Pass for tested paths. All reachable Git patch history and 125 tracked files scanned: zero exact matches for current bot token, webhook secret, DB URL or decoded DB password. Synthetic redaction tests pass. |
| DEPLOYMENT | Fresh non-root/read-only container, automatic migrations, SIGTERM, DB interruption, container replacement | Pass locally. Actual new-release Render deploy/redeploy and real Telegram delivery still pending. |

Relevant suites: `test_qa_adversarial.py`, `test_qa_inputs.py`, `test_business_*`,
`test_services.py`, `test_notifications.py`, `test_mobile_ux.py`, `test_form_navigation.py`,
`test_web_and_bot.py`, `test_production_lifecycle.py`, `test_deployment.py`, `test_ui_security.py`.

## Execution results

All counts below are executed tests; no skipped PostgreSQL tests count as a pass.

- New independent audit cases: **36** (21 integration + 15 calendar/text unit cases), all passing on PostgreSQL 17.
- Full frozen Docker suite: **423 passed in 151.54s**, coverage **74%**, zero skips/failures. Ruff lint/format, mypy (66 modules) and runtime dependency audit passed. The final rating-cursor overflow assertions also passed separately against PostgreSQL 17 (two parameter cases).
- Production image built successfully from the same frozen code; runs as UID 10001 with read-only
  root filesystem, temporary `/tmp`, dropped capabilities and no-new-privileges.
- Fresh PostgreSQL 17 database migrated to revision 0004 before readiness.
- Actual DB stop: `/health` 200, `/ready` 503 in 0.01s. DB restart: `/ready` recovered to 200.
- Eleven service-created tasks, a reserved rating cooldown and one in-flight batch survived DB restart
  and removal/replacement of the application container. Fresh service/engine claimed zero extra
  notifications during cooldown. No real Telegram send was made by this isolated test.
- Additional isolated screen assertions verified exactly 10,000 points (100%), over-target results and negative scores with the correct remaining target.
- SIGTERM logged complete application shutdown. Docker startup and replacement did not use local
  application files for tasks, cooldown or FSM persistence.

Reproduce:

```sh
docker build --target test -t taskflow:qa-test .
docker build --target production -t taskflow:qa-release .
# Only a disposable database ending in _test:
TEST_DATABASE_URL=postgresql://.../taskflow_audit_test make test
make lint
make typecheck
```

See [deployment](DEPLOYMENT.md) for the exact hardened container and live probe commands.

## Remaining risks and acceptance gates

1. Deploy the audited code with the documented old-consumer maintenance window. Verify revision 0004,
   new-release logs, health/ready, webhook secret rejection and getWebhookInfo. The old service's
   endpoints returned 200 and Telegram pending count was zero before this audit; an older Telegram
   read-timeout error remained recorded. That is not new-release delivery evidence.
2. Send a genuine authorized `/menu` and verify its fresh response; then redeploy and compare durable
   business fingerprints and inbox markers. Browser access must be restored to finish these checks.
3. Real Vika rating/completion/reopen acceptance was previously deferred by the user. Automated
   two-user tests do not replace phone testing with both accounts or inspection of actual Telegram
   rendering/message limits. Complete that acceptance before claiming the entire product verified.
4. Render Free sleeps: cold-start latency remains accepted by the user. Notifications trade possible
   omission after ambiguous sends/crashes for no duplicate spam; inspect `uncertain` rows rather than
   bulk replaying them. No exactly-once Telegram delivery guarantee is claimed.
5. Shared scoring timezone/current target preferences reinterpret historical displays as documented.
   The update worker serializes a two-user workload; a retried head update can temporarily delay
   later updates. This audit makes no high-load/DoS resilience claim or backup-retention guarantee.
6. Credential scan covers current known credentials and reachable Git history, not deleted remote
   refs, provider logs, unknown credentials or third-party backups. Existing local secret exports
   remain private outside Git for the pending deployment; remove them after completion.


## Documentation and clean-onboarding verification — 2026-10-07

README, AGENTS and PRODUCT/ARCHITECTURE/DATA_MODEL/DEPLOYMENT/OPERATIONS/TESTING were reconciled
with the implemented handlers, services, repositories, model constraints and Alembic head 0004.
Historical discovery/proposal claims in the architecture/product guides were replaced with current
behavior. All relative link targets in the nine handoff documents resolve. AGENTS includes an
operational DO NOT section and preserves scoring/access/migration/idempotency invariants.

### Additional finding QA-007 (Medium)

A fresh README setup with Compose's login named `taskflow` caused two existing migration tests to
fail. PostgreSQL's default `"$user", public` search path selected the application schema as the
connection's default; Alembic reflection then reported every application table as missing. This also
made autogeneration propose duplicate table creation. A new regression setting the disposable DB's
search path to `taskflow, public` reproduced the failure independently.

Fixed migration connections to explicitly use `search_path=public`; application DDL is still schema-
qualified. No schema revision or business-data change is required. The new test restores the
**disposable** database setting in finally. Applied migration files were not rewritten.

### Verification scope

Commands ran in a fresh temporary checkout copy with a new venv, Compose project/volume, local
PostgreSQL 16 and fake development Bot token. No production secrets or DB were used. Compose commands
used a separate `-p taskflow-docs` namespace to avoid existing services. The documentation pass's
one migration-env correction and regression are distinct from the preceding 423-test audit snapshot.

- `make dev`, fresh `.env`, `docker compose up -d db`, `pg_isready`, `make migrate`, `alembic current`:
  pass; empty database reached 0004. `make start` and `make run` both started successfully.
- Native `/health` and `/ready`: 200; unsigned webhook: 403. Startup seeded two users/seven types.
- `make migration message="documentation workflow smoke"`: generated a disposable revision with
  parent 0004; after the reflection fix `alembic check` reported no new operations. The disposable
  generated revision was removed without applying it.
- First native `make check`: **421 passed, 2 failed** (QA-007). Independent new regression: red.
- After correction, native `make check`: **424 passed, zero skips**, Ruff lint/format and mypy pass.
  Python 3.14/PostgreSQL 16 exercised the README's Python 3.12+ support path.
- `make test-business-unit`: **188 passed**.
- `make docker`, `docker compose up -d --build app`: production image/startup pass; container
  `/health` and `/ready` return 200; graceful Compose stop succeeds. Local queue-count SQL and
  `tools.deployment_probe --help` also pass.

- `make test-business`: **188 unit + 47 PostgreSQL cases passed**.
- Development HTTPS tunnel: `/health` and `/ready` 200, unsigned webhook 403. Default QUIC could
  not connect in this network; the documented `--protocol http2` fallback succeeded. The tunnel
  did not register the real bot or impersonate either user.
- After the fix, rerunning autogeneration against the seeded local DB produced an empty upgrade,
  with parent 0004. The disposable revision was removed without applying it.

- `make test-ux`: **63 passed** against separate disposable PostgreSQL 16.
- Final Docker test stage (Python 3.12/PostgreSQL 15): **424 passed**, zero skips, **74% coverage**;
  Ruff, mypy and runtime dependency audit passed. This includes the new search-path regression. Real BotFather credential entry, real two-user Telegram acceptance, new-release
Render deployment and actual Render redeploy persistence remain the existing external acceptance gates.
This documentation pass does not close the release decision above.
