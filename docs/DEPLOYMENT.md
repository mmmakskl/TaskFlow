# Production deployment

## Release gate and prerequisites

This is the deployment runbook, not a claim that the audited release is live. Check
[QA_REPORT.md](QA_REPORT.md) and [READINESS.md](READINESS.md) for dated evidence and remaining gates.
The first incompatible consumer upgrade has completed. Use the maintenance procedure below
when upgrading another legacy installation; compatible releases use checks-pass auto-deploy. Collaborative revision 0005 requires the same
stop-old-worker maintenance sequence, because old code cannot safely handle shared tasks/new events.

You need GitHub repository/admin access, Render and Supabase accounts, a BotFather token and the two
Telegram numeric IDs. For a new product, create a separate bot; never point an existing production
bot at a development tunnel. Both users must initiate the chat. Use Python 3.12+ with
`make dev` for the verification tool. Keep exported provider ENV/backup files outside Git, mode 0600.

## Current target

- Private source: `https://github.com/mmmakskl/TaskFlow`, branch `main`.
- Render Docker **Web Service**: `taskflow-private`, Frankfurt, approved Free plan.
- Service ID: `srv-db2mk82d0e5s73bm4sog`.
- Origin: `https://taskflow-private.onrender.com`.
- Database: external persistent Supabase PostgreSQL; application schema `taskflow`.
- Telegram: `https://t.me/taskflow_duo_bot`, secret-verified webhook; no polling.

Free instances sleep after inactivity, so cold starts can delay Telegram responses. The user accepted
this tradeoff. Do not select a paid plan without payment authorization. No Render disk is required.

## 1. Source and persistent database

For a fresh installation, create a private GitHub repository and push this project. Connect only that
repository to Render. CI must pass lint, types, real PostgreSQL tests, dependency audit and Docker build.

Create a Supabase project in a European region. Keep its database password in provider secrets.
Disable the Data API for this private application. **Never create application tables in its UI.**
Use Connect → **Session pooler, port 5432**, or a direct PostgreSQL connection with reachable networking.
Do not use Supabase transaction pooler 6543: migration session locks must retain their backend.

```text
postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@SESSION_POOLER_HOST:5432/postgres?sslmode=require
```

Ordinary `postgresql://` URLs also work with other persistent PostgreSQL providers. Production never
uses SQLite or the Render filesystem. URL-encode reserved password characters; enable TLS externally.

## 2. Render service and environment

In Render, authorize GitHub access to the private repository. Choose New → Blueprint to apply
`render.yaml`, or New → Web Service and select that repository for manual setup. Use branch `main`,
Dockerfile `./Dockerfile`, context `.`, health path `/ready`, and auto-deploy **After CI Checks Pass**.
Leave Docker Command empty: the image defines startup. Free services have no pre-deploy command;
migrations therefore run in the image startup before Uvicorn accepts traffic.

Configure these through Render Environment; never commit a production `.env`:

| Variable | Production value |
| --- | --- |
| `BOT_TOKEN` | Secret issued by BotFather |
| `WEBHOOK_SECRET` | Random URL-safe secret, 32–256 characters; Blueprint can generate it |
| `WEBHOOK_BASE_URL` | Assigned HTTPS origin, no path/query/credentials |
| `DATABASE_URL` | External persistent PostgreSQL URL with TLS |
| `USER_MAX_ID`, `USER_VIKA_ID` | Two distinct authorized numeric Telegram IDs |
| `USER_MAX_NAME`, `USER_VIKA_NAME` | Макс, Вика |
| `DEFAULT_TIMEZONE` | Europe/Helsinki, or the chosen IANA timezone |
| `DEFAULT_WEEKLY_TARGET` | 10000 |
| `RATING_NOTIFICATION_COOLDOWN_MINUTES` | 60 minimum |
| `RATING_NOTIFICATION_DEBOUNCE_SECONDS` | 5 |
| `ENVIRONMENT`, `LOG_LEVEL` | production, INFO |
| `REGISTER_WEBHOOK` | true; production rejects false |
| `PORT` | Supplied by Render; defaults to 10000 locally |

After Render assigns the service hostname, set `WEBHOOK_BASE_URL` to that exact HTTPS origin and
save/deploy the environment changes. Do not copy the current installation's URL for a new service.
The Blueprint generates `WEBHOOK_SECRET`; manual setup can generate one privately:

```sh
venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Both Telegram users must open the bot and press Start before it can send them notifications.
`REGISTER_WEBHOOK=false` is supported only for local development/tests and does not enable polling.

## 3. Startup, migrations and shutdown

The image runs `python -m app.startup` as non-root. It validates configuration, runs
`alembic upgrade head` via `app.db.migrate`, then replaces PID 1 with Uvicorn. Failure stops startup.
Migration connections use a PostgreSQL **session advisory lock** across Alembic commits, bounded
connection/command timeouts and no prepared-statement cache. Concurrent deployments cannot run DDL
simultaneously. Startup then seeds users/default types idempotently without overwriting preferences.

Webhook registration always sets the configured URL **and secret**, allows only message/callback
updates, uses five connections and preserves pending updates. Transient errors retry at most three
times with bounded delays; a permanent error prevents readiness. It never falls back to polling.

On SIGTERM, readiness becomes false, workers drain for up to 25 seconds, and Bot/storage/DB resources
close. Uvicorn has a 30-second graceful request timeout. Shutdown never deletes the webhook, because
an older Render replica may shut down after its replacement has registered the current configuration.

### First upgrade from the old notification consumer

Do not overlap the old consumer with producers of the new `penalty_created` event:

1. Temporarily set Render auto-deploy Off while merging the tested release into `main`.
2. Back up production and verify the backup restores into a disposable PostgreSQL database.
3. Suspend the old Web Service and wait for it to stop; Telegram retains pending webhook updates.
4. Select/deploy the tested `main` release and resume the service. Verify it starts the new commit.
5. Complete the checks below, then restore auto-deploy After CI Checks Pass.

This upgrade deliberately uses a short maintenance window. Future compatible releases can overlap;
notification claims and migration locks remain safe across replicas. Never deploy an untested head.

## 4. Local production-image verification

Use only disposable local PostgreSQL here. A real production BOT_TOKEN must not register a local URL.

```sh
docker build --target production -t taskflow:production .
docker network create taskflow-smoke
docker run -d --name taskflow-pg --network taskflow-smoke \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=taskflow_test postgres:17-alpine
```

Create a temporary ENV file **outside the repository** with a test-shaped Bot token, a 48-character
URL-safe webhook secret, test IDs 1001/1002, `ENVIRONMENT=development`, `REGISTER_WEBHOOK=false`, and
`DATABASE_URL=postgresql://postgres@taskflow-pg:5432/taskflow_test`. Trust authentication above is
strictly for this isolated disposable database, which exposes no host port.

```sh
docker run -d --name taskflow-smoke --network taskflow-smoke \
  --env-file /secure/path/local-smoke.env --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:18081:10000 taskflow:production
curl --fail http://127.0.0.1:18081/health
curl --fail http://127.0.0.1:18081/ready
curl -i -X POST -H 'Content-Type: application/json' \
  --data '{"update_id":1}' http://127.0.0.1:18081/telegram/webhook
docker logs taskflow-smoke
docker stop --timeout 40 taskflow-smoke
docker start taskflow-smoke
```

Expect health/ready 200 and unsigned webhook 403. With the matching secret, malformed JSON/update
returns 400 and a valid update returns 200 only after durable inbox commit. Duplicate update IDs
produce one inbox row. Verify processing, Alembic head and a service-created task survive restart.
Never use disposable test fixtures against the production URL.

## 5. Live validation and persistence

Wait for Render **Live** on the exact release commit. Inspect logs for `migration_complete`,
`webhook_configured`, successful startup and no recurring handler/notification/DB failures.
`GET /health` checks process liveness; `GET /ready` checks initialization, worker liveness and DB.
`POST /telegram/webhook` requires `X-Telegram-Bot-Api-Secret-Token`; no/incorrect secret returns 403.

Use the supplied probe with a securely exported Render ENV file (mode 0600, outside the repo):

```sh
venv/bin/python -m tools.deployment_probe --env-file /secure/path/production.env \
  --revision 0005 --exercise-webhook --receipt /secure/path/first-deploy.json
# Render Manual Deploy → Deploy latest commit, then wait for Live again.
venv/bin/python -m tools.deployment_probe --env-file /secure/path/production.env \
  --revision 0005 --exercise-webhook --baseline /secure/path/first-deploy.json \
  --receipt /secure/path/after-redeploy.json
```

The probe checks HTTPS endpoints, unsigned rejection, signed duplicate inbox processing, revision
0005, Telegram getWebhookInfo and business-data fingerprints. Its signed test is an empty synthetic
update with a reserved negative ID: it does not impersonate a user or send a chat message. A previous
receipt's marker must still exist after redeploy. Receipts contain counts/hashes, never task text or
secrets. Avoid changing business records between receipts when comparing their exact fingerprints.

Also send a real `/menu` from an authorized Telegram account and verify a fresh response, then inspect
inbox/logs/getWebhookInfo for successful delivery. An old `last_error_date` can remain after recovery;
check pending count and whether newer updates were delivered. Synthetic HTTP checks alone do not
prove Telegram-to-Render delivery. Recheck health/ready and existing tasks after a second deployment.
Record actual results and any deferred two-account product acceptance in `docs/READINESS.md`.

## 6. Rollback and troubleshooting

Prefer a forward fix or roll back application code compatible with the current schema. Never run a
destructive downgrade on production. 0004 downgrade removes diagnostics, so first stop workers and
back up; legacy notification consumers require the maintenance procedure above. Details:
[operations](OPERATIONS.md), [notifications](NOTIFICATIONS.md).

For readiness failure, check DB URL/TLS, pooler port, migration revision and worker logs. For webhook
failure, check origin/secret, Telegram bot permissions, blocked chats and cold starts. Do not clear
pending Telegram updates, bulk replay uncertain notifications, log tokens or enable public debug UI.
See [Render deploys](https://render.com/docs/deploys) and
[Telegram webhook parameters](https://core.telegram.org/bots/api#setwebhook).


## Evidence to hand off

Record the exact source/deployed commit, CI run, Alembic revision, Live event, endpoint results,
webhook delivery timestamps, actual two-account acceptance and before/after redeploy receipts.
Do not record credentials or private task text. [QA_REPORT.md](QA_REPORT.md) is the audit decision;
[READINESS.md](READINESS.md) is the chronological release evidence. Old discovery plans under
`docs/superpowers/plans/` are historical planning notes, not the source of current deployment status.

## Collaborative 0005 release verification

Back up before maintenance; validate restoration and0004 → 0005 migration in a disposable database.
Disable auto-deploy, stop the0004 service, then deploy the CI-tested commit and verify0005 before
allowing new tasks. Do not overlap0004 and 0005 workers or roll back to0004 code after collaborative
data is written. Schema 0005 refuses destructive downgrade. Historical task fingerprint comparison
must project the original columns, because 0005 adds identity/provenance columns without changing
legacy task content. Full current-schema fingerprints are appropriate for subsequent redeploys.
