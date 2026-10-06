# Operations

## Health and logs

Check `GET /health` for process liveness and `GET /ready` for startup readiness plus a live database query. Render uses `/ready`. Logs include startup/shutdown, update IDs, notification IDs and stack traces with secret redaction. There is no public debug endpoint or API documentation.

Use Render Events to inspect build/deploy failures, and Logs for migration, database, webhook and handler errors. Never paste token-bearing URLs or database passwords into issues or shared logs.

## Restart and redeploy

Render Manual Deploy → Deploy latest commit restarts the Docker Web Service. Persistent data and FSM stay in Supabase. Startup reseeding preserves modified penalty rates, targets and timezones. Shutdown leaves the webhook registered to avoid an old replica clearing a newer deployment's configuration.

## Webhook recovery

Inspect Telegram getWebhookInfo privately: URL, pending_update_count and last_error_message. Confirm WEBHOOK_BASE_URL points at the current service and WEBHOOK_SECRET matches startup registration. Redeploy after correcting ENV. Both users must start the bot; Telegram cannot notify a user who has not initiated a chat. Check blocked-bot errors separately from webhook errors.

Inbox pending rows retry with backoff and become failed after five attempts. Inspect the update ID and fix the cause before requeuing a specific failed row. Never bulk replay production updates: stale callbacks and side-effect delivery may have different outcomes. Processed payloads are cleared; completed inbox rows older than seven days are pruned.

Notifications in `sending` after a crash are intentionally not retried because sendMessage cannot prove whether Telegram already delivered them. Failed notifications are visible in logs; users can always open pending ratings from the menu. Rating cooldown starts at send attempt, not confirmed delivery.

## Database and migrations

Use the Supabase session pooler on 5432, TLS and correctly encoded passwords. `/ready` failure may indicate pooler credentials, project availability or migration failure. Migrations run before the web server with a session advisory lock. Inspect Alembic revision and schema before retrying; never create tables manually.

The application uses a bounded pool of three connections plus two overflow connections per replica. Keep replica count small for this two-user service. Supabase owns persistence; configure provider backups appropriate to the account plan and periodically validate restores into a separate project. Free hosting is not a backup guarantee.

## Rollback

Render can redeploy the last known-good commit. Roll back application code only when compatible with the current schema. Prefer a forward corrective migration; the initial downgrade refuses destructive removal. Snapshot/backup production before any data migration. Never edit audit/score history: insert compensating business events through reviewed code.

## Security changes

Changing authorized IDs changes who can see both users' records. Review it deliberately. Rotate exposed credentials through their providers and then update Render secrets. A database owner account is required for initial migrations; a separate least-privilege runtime role is a sensible operational hardening step when distinct migration/runtime URLs are introduced.
