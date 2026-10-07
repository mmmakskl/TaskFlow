# Fresh production review — 2026-10-07

## Scope and method

Read application modules, models, migrations, startup, Docker, Render and CI configuration before
consulting earlier reports. Rechecked the actual implementation against access, scoring, snapshot,
transaction, notification and mobile UI invariants. Historical reports are evidence, not acceptance.

## Findings fixed

- **Medium — ASGI log redaction bypass.** Uvicorn's own handlers bypassed the application's secret
  and PostgreSQL DETAIL redaction. Route its error/access handlers through the redactor while
  preserving `--no-access-log`. Regression tests exercise both access-log policies and DB errors.
- **Medium — denied sender poisoned inbox retries.** Telegram refusing the private-access courtesy
  reply could delay subsequent authorized updates. Denial delivery is now best effort, without
  invoking handlers or retrying that unauthorized update. Unit and durable-inbox tests cover it.
- **Low — malformed persisted draft.** A naive deadline or an oversized flow ID could survive draft
  validation after restart and leave unusable buttons. Reject both and recover to a valid screen.

No schema changes were needed for these fixes. Ten new regression cases cover these defects, maximum
Unicode screen/callback sizes, exact production settings with worker processing, and four concurrent
DONE/reopen cycles checked against the database ledger.

## Verification ledger

The working tree includes an unrelated callback-validation patch and 20 tests, intentionally excluded
from this release. Report working-tree and frozen-release results separately. Integration runs use
only disposable PostgreSQL databases ending in `_test`; never reset production schemas.

Fresh verification for application commit `576f08a`:

| Gate | Result and evidence |
| --- | --- |
| Working-tree full suite | PASS: 454 tests, zero skips; Python 3.13/PostgreSQL 16, 228.65 s |
| Frozen release suite | PASS: 434 tests, zero skips; Python 3.12/PostgreSQL 15, 298.22 s, 75% coverage |
| Ruff / format / mypy | PASS in native and Docker runs; 66 typed application files |
| Dependency audit | PASS: no known runtime dependency vulnerabilities |
| Production Docker | PASS: image builds; UID 10001, read-only root, dropped capabilities |
| Empty migrations | PASS: 0001 through 0004 on isolated PostgreSQL 17 |
| Real backup restore | PASS: Supabase dump restored into isolated PostgreSQL 17 |
| Safe migration round trip | PASS: restored 0002 → 0004 → 0002 → 0004; business fingerprints identical |
| DB interruption | PASS: health 200, ready 503; ready returns 200 after DB restart |
| App restart / persistence | PASS: ten service-created tasks retained, Alembic 0004 retained |
| Webhook transport | PASS locally: missing secret 403, malformed input 400, signed duplicate 200 with one processed inbox row |
| Secret scan | PASS: configured production credentials absent from tracked files and reachable Git patches |
| Real Render release / redeploy | PENDING: old live commit d2e4cee, revision 0002; new release requires rollout |
| Real two-account Telegram acceptance | PENDING: previously deferred by user; not inferred from mocked transport |

Local evidence: `/private/tmp/taskflow-review-final-check.log`,
`/private/tmp/taskflow-review-final-docker.log`, `/private/tmp/taskflow-review-production-build.log`.
Production backups/receipts are mode 0600 outside Git. Do not publish their contents.

GitHub CI passed for application commit 576f08a. Live evidence must be updated after rollout. Do not infer new-release acceptance
from the older service's healthy endpoint.

## Review conclusions

Services own authorization and atomic mutations; repositories own bounded queries and row/advisory
locks. Score events are immutable, use completion timestamps and compensating reopen/deletion
entries. Penalties retain snapshots. Week boundaries are aware, local Monday boundaries converted
into half-open UTC intervals. Notification batching/cooldown and FSM/inbox are PostgreSQL durable.
Webhook secrets are checked before parsing; two configured private-chat identities are enforced.
Outgoing maximum Unicode screens and 64-byte callbacks are covered by regression tests.

Residual operational limitations: Free Render cold starts, provider free-tier availability/backup
limits, intentionally uncertain delivery after ambiguous Telegram network failures, and outstanding
real acceptance from the second account. Automated peer-flow tests do not replace that acceptance.

## Production readiness checklist

| Area | Status | Evidence / remaining gate |
| --- | --- | --- |
| Architecture | PASS | Handlers → services → repositories; durable inbox/outbox |
| Correctness / scoring | PASS | Independent SQL ledger reconciliation and concurrent DONE/reopen cycles |
| Data integrity / history | PASS | Snapshots, append-only audit/score events and compensating entries |
| Security | PASS | Allow-list/private-chat enforcement, secret-first webhook validation, redacted ASGI logs, Git secret scan |
| Telegram UX implementation | PASS | FSM/handler tests, stale/deleted messages, optional skips/back, pagination and Unicode limits |
| Transactions | PASS | Atomic service mutations, row locks, operation advisory locks and rollback tests |
| Idempotency | PASS | Duplicate inbox/action tests and four-way concurrent transitions |
| Timezone | PASS | Aware dates, Monday half-open boundaries and DST tests |
| Notification batching | PASS | Persistent cooldown, recipient locking, debounce, duplicate/race/restart tests |
| Migrations | PASS | Empty DB, restored live data, guarded safe round trip; never downgraded production |
| Docker / production-like runtime | PASS | Build, non-root/read-only runtime, health/ready, DB failure, app restart and persistence |
| Documentation | PASS | Commands/config match implementation; historical claims explicitly separated from latest evidence |
| Automated tests | PASS | 434 release tests / 454 working-tree tests, no skips |
| Lint / types / dependencies | PASS | Ruff, formatting, mypy and pip-audit |
| GitHub CI | PASS | Run [37583671760](https://github.com/mmmakskl/TaskFlow/actions/runs/37583671760), application commit 576f08a |
| New Render release deployment / logs | FAIL | Not yet deployed; UI automation blocked, API credential requested |
| Actual Render redeploy persistence | FAIL | Only isolated production-image restart/persistence verified so far |
| Genuine new-release Telegram smoke | FAIL | Web client cannot be controlled reliably; no fabricated sender updates used |

No known unresolved blocker/high-severity code defect was found in the reviewed paths. The release
remains unaccepted until the live FAIL gates above are closed. Real Vika rating → Max completion
acceptance was previously deferred and still requires the second authorized account.
