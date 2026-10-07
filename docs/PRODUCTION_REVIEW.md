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

Final test counts, live deployment evidence and remaining acceptance gates are recorded below after
verification. Do not infer live acceptance from an older healthy endpoint.

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
