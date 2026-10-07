# Operations

## Health and logs

Check `GET /health` for process liveness and `GET /ready` for startup readiness plus a live database query. Readiness also checks the worker task and bounds the DB probe to five seconds. Render uses `/ready`. Logs include startup/shutdown, update IDs, notification IDs and stack traces with secret redaction. There is no public debug endpoint or API documentation.

Use Render Events to inspect build/deploy failures, and Logs for migration, database, webhook and handler errors. Never paste token-bearing URLs or database passwords into issues or shared logs.

## First response

1. Identify the **deployed commit** in Render Events; compare it with the intended release and CI.
2. Check endpoints without secrets:

   ```sh
   curl --fail --max-time 90 https://taskflow-private.onrender.com/health
   curl --fail --max-time 90 https://taskflow-private.onrender.com/ready
   ```

3. Inspect Render Logs for `migration_complete`, `webhook_configured`, `update_failed`,
   `update_dead_letter`, `notification_delivery` and worker/DB failures. Use IDs to correlate events.
4. Check [QA_REPORT.md](QA_REPORT.md) and [READINESS.md](READINESS.md) before declaring recovery.
   Runtime deployment is verified; genuine two-account acceptance remains separately documented.

The Free service may need a cold start; health does not prove delivery. Do not enable polling,
clear pending updates, change access IDs or replay uncertain notifications as a troubleshooting shortcut.

## Restart and redeploy

After CI passes for the intended commit, Render Manual Deploy → Deploy latest commit restarts the Docker Web Service. For the first legacy notification-consumer upgrade, stop the old consumer as described in [DEPLOYMENT.md](DEPLOYMENT.md); do not overlap it with new producers. Persistent data and FSM stay in Supabase. Startup reseeding preserves modified penalty rates, targets and timezones. Shutdown leaves the webhook registered to avoid an old replica clearing a newer deployment's configuration.

## Webhook recovery

Inspect Telegram getWebhookInfo privately: URL, pending_update_count and last_error_message. Confirm WEBHOOK_BASE_URL points at the current service and WEBHOOK_SECRET matches startup registration. Redeploy after correcting ENV. Both users must start the bot; Telegram cannot notify a user who has not initiated a chat. Check blocked-bot errors separately from webhook errors.

Inbox pending rows retry with backoff and become failed after five attempts. Inspect the update ID and fix the cause before requeuing a specific failed row. Never bulk replay production updates: stale callbacks and side-effect delivery may have different outcomes. Processed payloads are cleared; completed inbox rows older than seven days are pruned.

Notifications in `sending` after a crash become `uncertain` after five minutes and are intentionally not replayed because sendMessage cannot prove whether Telegram already delivered them. Failed notifications are visible in logs; users can always open pending ratings from the menu. Rating cooldown starts at the reserved send attempt, not confirmed delivery. New rating batches inside cooldown wait until it expires. See [notification delivery](NOTIFICATIONS.md) for error codes, retry policy and migration 0004.

## Database and migrations

Use the Supabase session pooler on 5432, TLS and correctly encoded passwords. `/ready` failure may indicate pooler credentials, project availability or migration failure. Migrations run before the web server with a session advisory lock. Inspect Alembic revision and schema before retrying; never create tables manually.

The application uses a bounded pool of three connections plus two overflow connections per replica. Keep replica count small for this two-user service. Supabase owns persistence; configure provider backups appropriate to the account plan and periodically validate restores into a separate project. Free hosting is not a backup guarantee.

## Rollback

Render can redeploy the last known-good commit. Roll back application code only when compatible with the current schema. Prefer a forward corrective migration; the initial downgrade refuses destructive removal. Snapshot/backup production before any data migration. Never edit audit/score history: insert compensating business events through reviewed code.

## Security changes

Changing authorized IDs changes who can see both users' records. Review it deliberately. Rotate exposed credentials through their providers and then update Render secrets. A database owner account is required for initial migrations; a separate least-privilege runtime role is a sensible operational hardening step when distinct migration/runtime URLs are introduced.

## Business migration rollback

Revision 0003 can return to 0002 without removing rows or estimate columns, but this removes its new
constraints and snapshot guard. Prefer an application-only rollback with the guards retained. Test
any explicit downgrade/upgrade on a restored disposable database before maintenance. Revisions
0002 and 0001 reject destructive downgrade. If 0003 rejects inconsistent existing data, leave the
failed deployment stopped, inspect the named constraint privately, and prepare a reviewed corrective
migration; do not disable checks or rewrite immutable score/audit events. See [BUSINESS.md](BUSINESS.md).


## Inspect schema and queues safely

For local development only (production inspection uses a private provider connection):

```sh
venv/bin/python -m alembic current
docker compose exec -T db psql -U taskflow -d taskflow -c \
  'SELECT status, count(*) FROM taskflow.telegram_updates GROUP BY status'
docker compose exec -T db psql -U taskflow -d taskflow -c \
  'SELECT kind, status, count(*) FROM taskflow.notifications GROUP BY kind, status'
```

Inspect counts/IDs/statuses first, not full private payloads. Current migration head is 0006;
`public.alembic_version` records it. Use `make migrate` locally; Render's image runs the same upgrade
before accepting requests. Never run fixtures, schema resets or integration tests on production.

## Backup, restore and rollback sequence

Before a breaking rollout, stop incompatible consumers, take a provider or PostgreSQL custom-format
backup and restrict its file permissions to 0600 outside Git. Include both application schema and
its Alembic revision table; preserve any provider-owned schema separately through provider backups.
Validate restore into an isolated database, compare business counts/fingerprints and run migrations
there. A dump existing on disk without a tested restore is insufficient evidence.

If rollout fails, leave incompatible workers stopped. Restore service with a schema-compatible
application revision or a forward fix. Do not select an old Render image merely because it once ran:
verify its notification kinds and schema expectations. 0004 downgrade removes diagnostics; 0003
downgrade removes guards; 0002/0001 refuse destructive rollback. Never use production downgrade as
an experiment or disable triggers to make it pass.

Once live, run the [deployment probe](DEPLOYMENT.md#5-live-validation-and-persistence), genuine
Telegram acceptance and a same-code redeploy persistence check. Record commit, revision, timestamps
and private receipt location in READINESS. Remove temporary credential exports when finished; retain
backups only in private storage with an explicit retention policy. Production credentials must never
appear in shell history, screenshots, issue bodies or tracked documentation.
