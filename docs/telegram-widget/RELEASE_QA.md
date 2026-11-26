# Chat release verification — 2026-11-26

Scope: standalone chat navigation, ordinary HTML transport, disabled future widget,
command menus, callback notifications and three pricing modes in chat template management.
No schema change or dependency upgrade is required. The standalone Mini App remains available.

Implementation is based on `origin/main` at `39a5380`, including activity cards from PR #13.
The original checkout's uncommitted files were preserved in place; implementation uses a separate
`feat/chat-widget-flag` worktree.

## Independent review

Architect, backend implementer, UI/UX designer, QA and independent reviewer worked with separate
file ownership. Final review approved specification compliance and code quality with no open
required findings. Review found and verified fixes for two historical-keyboard cleanup issues:

- Startup cleanup now acquires the same PostgreSQL transaction advisory lock as inbox workers
  before reading FSM data. A rolling deployment cannot overwrite a newer draft with a stale JSON
  snapshot while waiting on Telegram.
- Cleanup sanitizes the pending form's own keyboard independently from the currently visible
  keyboard, preserving its original controls and fields.

A real PostgreSQL concurrency regression holds the worker lock, commits a newer draft/message ID,
and verifies cleanup waits and preserves distinct current/pending controls. Additional malformed
snapshot regressions caught and fixed an exception from unhashable field/mode values.

## Automated acceptance

`BotDriver` now checks all integration chat outputs for absence of Rich Message transport,
WebApp buttons and Mini App URLs. Regression coverage includes `/start`, `/menu`, command/menu
scopes, fallback recovery, form interruption, five-item lists, confirmations, authorization,
shared scoring, stale and repeated callbacks, notifications, and fixed/time/quantity template
creation, use and editing. Configuration tests cover default false, explicit false and rejected
true values, including ENV loading. Cleanup tests cover authorized saved IDs, failed Telegram
edits, malformed data and independent pending snapshots.

Final results (all commands exited zero; no skipped tests):

| Gate | Result |
| --- | --- |
| `make check` | Ruff lint/format, mypy (75 source files), 769 Python tests and 10 JavaScript tests passed |
| `make test-business-unit` (included by business gate) | 262 passed |
| `make test-business` integration selection | 63 passed |
| `make test-ux` | 113 Python tests and 10 JavaScript tests passed |
| `pip-audit -r requirements.txt --disable-pip --no-deps` | No known vulnerabilities |
| `make docker` | Production image built |
| `docker build --target test -t taskflow:chat-widget-test .` | Audit/lint/types/JS and 769 Python tests passed on Python 3.12 + isolated PostgreSQL 15; 71% coverage |
| `git diff --check` | Passed |

Local Python gates used Python 3.13 and a disposable PostgreSQL 16 container; Docker independently
verified the supported Python 3.12 runtime. Destructive integration tests used only disposable
PostgreSQL databases ending in `_test`; no production database was accessed. Static mockup checks
and their real-client limits are documented in [VALIDATION](VALIDATION.md).

## Real-client and rollout limits

This change has not been merged or deployed. Live Telegram two-account delivery, menu appearance,
historical-message editability and persistence after production restart remain acceptance steps
in [DEPLOYMENT](../DEPLOYMENT.md). Prior rollout records do not confirm this change.

Only persisted screen message IDs can be cleaned. Historical launcher and notification IDs were
not stored, so some old messages may retain buttons; Telegram can also refuse edits. No history
scan is performed. `/start` and `/menu` remove the old persistent reply keyboard and open chat
controls. No running widget is shipped: `TELEGRAM_HTML_WIDGET_ENABLED=true` is rejected.
[Static mockup validation](VALIDATION.md) does not establish actual Telegram Widget API or assistive
technology compatibility.
