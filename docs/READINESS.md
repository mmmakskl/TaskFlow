# Production readiness evidence

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
