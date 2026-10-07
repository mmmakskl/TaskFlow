# Production readiness evidence

## Live rollout — 2026-10-07

Runtime release `d783f1da1cfe5f7c22681d33e893cc375b86a7f8` (PR #4) is deployed on
Render Free. Deployment `dep-db2ut5d9fdbs739ctq50` reached Live at 07:04:07 UTC.
Logs show migrations 0002 → 0003 → 0004, production startup and `webhook_configured`.
Health/ready return 200; wrong-secret rejection and signed duplicate inbox processing pass.
Telegram reports the expected webhook, zero pending updates and no last delivery error.
Business fingerprints match the pre-upgrade baseline: six tasks, two penalties, seven penalty
types, sixteen audit events and four score events. A private backup was taken before maintenance.

Same-code redeploy `dep-db2uu92d0e5s73ehknu0` also reached Live. The second receipt confirms
identical business fingerprints, retention of the first processed webhook marker, and successful
processing of a new duplicate marker. Health/ready remain 200, revision is 0004 and Telegram reports
zero pending updates/no last error. Auto-deploy is restored to **After CI Checks Pass**.
Private receipts/backups are in `~/.local/share/taskflow/deployment/2026-10-07/live-rollout/`.

Genuine new-release two-account Telegram acceptance remains deferred; synthetic transport probes
do not impersonate either user. Earlier closed-rollout sections below are historical.

## Fresh final review

See [PRODUCTION_REVIEW.md](PRODUCTION_REVIEW.md) for the fresh code-first audit, three additional
fixes and current verification. Earlier sections below are historical checkpoints.

## Documentation handoff verification — 2026-10-07

README and all required handoff guides now describe the actual revision-0004 application.
Clean native onboarding (Python 3.14/PostgreSQL 16) and Docker test stage
(Python 3.12/PostgreSQL 15) each pass **424 tests**, zero skips.
The pass exposed and fixed Alembic reflection for a login/search path named `taskflow`;
see QA-007 and exact command evidence in [QA_REPORT.md](QA_REPORT.md).
HTTPS development tunnel and native/Compose health/ready/unsigned-webhook checks passed.
This is local verification, not new-release Render or real two-account Telegram acceptance.
The production rollout gate below remains open work.


## Latest release gate — independent QA, 2026-10-07

**Not production ready:** the audited new release still requires its Render rollout, live Telegram
webhook delivery and persistence check after actual redeploy. See [QA_REPORT.md](QA_REPORT.md) for
six reproduced/fixed findings, the scenario matrix and remaining risks. The final frozen Docker
suite passed **423 tests**, coverage 74%, lint/types and dependency audit. Thirty-six new independent
cases passed against PostgreSQL 17; actual isolated PostgreSQL restart and application-container
replacement preserved eleven tasks and the rating cooldown. Older evidence below is historical and
does not establish acceptance of this new release.

## Verified before deployment

- Private GitHub repository: `mmmakskl/TaskFlow`.
- Supabase project provisioned in Ireland; Data API disabled. No tables created manually.
- First full Docker test build: 49 tests passed against real PostgreSQL15 on Python3.12; Ruff formatting/lint and mypy passed. Coverage62% at that checkpoint.
- Final review fixes verified: 59 tests passed in Docker, including expired callbacks and live MANUAL penalty configuration; coverage63%. Ruff and mypy pass.
- Runtime dependency audit: no known vulnerabilities (`pip-audit` in Docker). Production image builds.
- Uvicorn smoke: health/ready200, missing webhook secret403, task survives two process starts.
- Local unit suite: 40 passed; integration is deliberately skipped without a dedicated test database.

## Current gates

Infrastructure, CI, HTTPS endpoints, real Max /start/task creation and task persistence after redeploy are verified. Real Vika rating/completion/reopen acceptance is deferred by the user. Max penalty/config/snapshot/cleanup smoke is verified on production. Do not interpret provisioning or mocked chat tests as production completion.

## Design limits

Notifications favor at-most-once send attempts over guaranteed delivery after ambiguous network failures. Historical target display uses current preferences. Shared scoring timezone changes reinterpret historical week boundaries. The two-user worker intentionally serializes updates; no scale claims are made. Supabase backup retention depends on the selected account plan.

- Hosting decision: user explicitly selected Render Free, accepting cold-start delays. Service `srv-db2mk82d0e5s73bm4sog` is linked to private GitHub main in Frankfurt.

## First live deployment

- Render deployed commit045dfbc successfully; startup completed against Supabase and registered Telegram webhook. Public origin: https://taskflow-private.onrender.com.
- GitHub Actions run37535209965 passed install, lint, types, real PostgreSQL16 tests, dependency audit and Docker build.
- Browser observed /health `{"status":"ok"}` and /ready `{"status":"ready"}`. Production HTTP smoke run37535639724 passed against live HTTPS, including unauthorized POST403.
- Telegram getWebhookInfo: exact /telegram/webhook URL, pending_update_count0, no last_error_message, only message/callback_query updates.
- User confirmed real Telegram response and task creation. Read-only Supabase check found revision0001, two users, seven penalty types and taskid1 BACKLOG created2026-10-06T21:41:58.416543Z.
- Render subsequently deployed a36c821 successfully after CI37535639698 passed. After that redeploy, Telegram Web K displayed the same owner taskid1 (BACKLOG) in the real task list, confirming production task persistence.
- Desktop Telegram automation was rejected by automatic approval review ("Computer Use was not approved to use Telegram"). Fallback: the user authorized Telegram Web by scanning QR; Web K allows direct bot UI testing. The user explicitly deferred real Vika peer-flow acceptance because it cannot be checked now; automated PostgreSQL/aiogram peer-flow tests passed.

## Final production smoke

- Real Max UI: /start, navigation, task list/details, optional penalty comment skip and destructive confirmation work through Telegram Web K.
- Instagram12 minutes at100/min produced1200 points and net−1200. Updating rate to150 through settings required no restart; a new one-minute penalty produced150, while the old penalty remained1200 (net−1350).
- Test penalties were soft-deleted with confirmations; compensating ledger events returned score to0. Instagram rate restored to100.
- Read-only production SQL verified: revision0001, taskid1 unchanged BACKLOG with identical creation timestamp, penalty snapshots1200/rate100 and150/rate150 retained with deleted=true, active_penalties0, four score events totaling0, nine audit events, failed inbox updates0.
- Final expanded Docker suite:61 passed, coverage67%, including history aggregation and unexpected-handler retry/one safe notification. Ruff, mypy and runtime dependency vulnerability audit passed.
- The user chose to defer real Vika peer-rating/completion/reopen acceptance. Those flows are verified by actual PostgreSQL and aiogram tests, not claimed as live two-account chat checks.

No external secrets remain unconfigured. Render Free cold-start delays are accepted by the account owner. Outstanding invoices shown by Supabase belong to another organization; TaskFlow provisioned and operated successfully, and no payment action was performed.

## Production startup release — 2026-10-07

- Release PR: https://github.com/mmmakskl/TaskFlow/pull/4, code commit `fe20aa8`.
- GitHub Actions run `37575930948` passed all checks: Ruff, mypy, PostgreSQL tests,
  dependency audit and production Docker build. The frozen release Docker test target passed
  **387 tests**, coverage 73%, on Python 3.12 with PostgreSQL 15.
- The final production image starts as UID 10001 with a read-only root filesystem, dropped
  capabilities and external PostgreSQL 17. Health and readiness return 200. Fresh startup
  applies revisions 0001–0004 before accepting requests. Signed duplicate synthetic updates
  produce one processed inbox record; unsigned requests return 403.
- A locally created task and processed inbox record survived container shutdown, restart and
  replacement with the final release image. SIGTERM completed graceful worker/resource shutdown.
- Production PostgreSQL 17 backup restored successfully into an isolated PostgreSQL 17 database;
  upgrading the restored schema from 0002 to 0004 retained all three tasks and four score events.
  Backup and pre-deploy business fingerprints are retained privately outside the repository.
- `render.yaml` passed Render's published JSON Schema. Production startup validates configuration,
  locks migrations, ensures the webhook URL and secret, and fails readiness if its worker stops.

### Release status

The new release has **not yet been deployed**. The previously live service remains on its older
release and schema 0002. Render auto-deploy was temporarily switched Off to prevent incompatible
old/new notification consumers overlapping. Complete the maintenance procedure in DEPLOYMENT.md,
then restore After CI Checks Pass. Browser control became unavailable (`cgWindowNotFound`) before
release rollout; Render logs, new-release Telegram delivery and production redeploy persistence
must still be verified. Local Docker checks and the backup restore do not prove those live checks.
