# Deployment

## 1. Private source and CI

Create a private GitHub repository and push `main`, including `.github/workflows/ci.yml`, Dockerfile and migrations. GitHub Actions installs pinned dependencies, runs Ruff/mypy and real PostgreSQL tests, audits dependencies and builds production Docker. Grant Render access only to this repository.

## 2. Persistent Supabase PostgreSQL

Create a Supabase organization/project in a European region. Generate a strong database password and keep it only in provider secrets. Disable the Data API for this private application. Do not create tables in the dashboard.

Open Connect → Session pooler and copy the IPv4 connection details, port **5432**. Build a URL with URL-encoded password and TLS:

`postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@SESSION_POOLER_HOST:5432/postgres?sslmode=require`

Do not use transaction pooler 6543: migration session advisory locks must stay on the same PostgreSQL backend through commits. A direct connection is also supported where IPv6 is available.

## 3. Telegram

Create the bot in @BotFather, record its token securely and obtain the two authorized numeric Telegram IDs. Both people must open the bot and press Start. Only their ENV IDs are permitted; usernames do not authorize access.

## 4. Render Web Service

Connect the private GitHub repository. Create a Docker **Web Service**, branch `main`, region Frankfurt, Dockerfile `./Dockerfile`, health path `/ready`, auto-deploy after checks pass. Use the always-on Starter plan for prompt Telegram responses; a Free service sleeps and is unsuitable for predictable production latency. Paid plans require the account owner's payment approval.

Alternatively use the checked-in `render.yaml` Blueprint. Generate `WEBHOOK_SECRET`; fill BOT_TOKEN, DATABASE_URL, USER_MAX_ID and USER_VIKA_ID as secrets. Set WEBHOOK_BASE_URL to the assigned `https://SERVICE.onrender.com` origin. Set names, Europe/Helsinki, target 10000, cooldown 60, debounce 5, ENVIRONMENT=production and LOG_LEVEL=INFO. REGISTER_WEBHOOK defaults true.

Docker's startup command runs `python -m app.db.migrate`, then Uvicorn on `$PORT`. A PostgreSQL advisory lock prevents concurrent migration runs. Startup safely seeds defaults and users, registers the webhook and becomes ready. No Render filesystem or disk is required for persistence.

## 5. Acceptance

1. Wait for service status Live and check migration/startup logs.
2. GET `/health` and `/ready` must return 200.
3. POST `/telegram/webhook` without secret must return 403.
4. Telegram getWebhookInfo must show the exact HTTPS webhook URL and no repeated delivery error.
5. Run the acceptance flow in `docs/PRODUCT.md` using both actual Telegram accounts.
6. Redeploy the same commit; verify the task, score, cooldown and draft state still exist.
7. Verify unauthorized Telegram access reveals no data.

Do not call deployment complete on health checks alone. Preserve evidence and unresolved external gates in `docs/READINESS.md`.
