# Execution ledger

- Research: pinned MIT reference inspected; only pagination pattern adapted with attribution.
- Infrastructure: private repository and Supabase project created; Render GitHub connection and service configuration available.
- Core implementation: schema, services, durable FSM/inbox/outbox, webhook, UX and CI implemented.
- Evidence: Docker PostgreSQL suite49/49 at first checkpoint; lint and mypy green. Deployment still pending.
- Ruling: use an append-only signed scoring ledger with reversals — preserves immutable historical events and correct reopen weeks — cost if wrong: extra storage/queries.
- Ruling: commit notification send claims before Telegram and do not retry ambiguous outcomes — Telegram has no sendMessage idempotency key, user requires no spam — cost if wrong: an occasional omitted reminder.
- Ruling: scoring weeks use one ENV timezone while per-user timezone controls display — shared weekly comparisons need one calendar — cost if wrong: users must change shared ENV to move scoring boundaries.
- Ruling: inspect only taskflow schema during Alembic autogenerate — avoids provider-owned schema mutations — cost if wrong: cross-schema changes need explicit migrations.

- Fresh reviewer found and fixed: expired Telegram callback ack rollback, MANUAL type zero-rate validation, SQLAlchemy parameters/PostgreSQL DETAIL leakage, penalty UTC display, and external notification calls in subprocess test. Regression tests added.
- Expanded checkpoint: 53 PostgreSQL tests passed, including actual Uvicorn start/stop/restart persistence; production Docker image built; pip-audit found no known runtime dependency vulnerabilities. Subsequent review fixes await full rerun.

- Final correction pass:59/59 PostgreSQL tests green; Ruff/mypy and runtime dependency audit pass. SQL parameters/server DETAIL hidden; expired acknowledgments do not roll back mutations.

- Ruling: use user-selected Render Free — owner declined paid always-on plan — cost if wrong: cold-start delays, later paid upgrade needs approval.
