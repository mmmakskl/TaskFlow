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

Deployment, GitHub Actions result, real Telegram acceptance and persistence after Render redeploy remain pending. Do not interpret provisioning or mocked chat tests as production completion.

## Design limits

Notifications favor at-most-once send attempts over guaranteed delivery after ambiguous network failures. Historical target display uses current preferences. Shared scoring timezone changes reinterpret historical week boundaries. The two-user worker intentionally serializes updates; no scale claims are made. Supabase backup retention depends on the selected account plan.

- Hosting decision: user explicitly selected Render Free, accepting cold-start delays. Service `srv-db2mk82d0e5s73bm4sog` is linked to private GitHub main in Frankfurt.
