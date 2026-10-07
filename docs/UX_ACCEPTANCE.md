# Mobile UX acceptance

## Scope and implementation

All requested flows use aiogram routers, PostgreSQL FSM, inline keyboards and a single edited menu.
Existing business services still decide permissions, transitions, snapshots, points and idempotency.
The collaborative extension uses Alembic revision `0005`; existing task, score and audit history is preserved.
The earlier mobile inspection below predates this extension and is not visual acceptance of the new screens.

| Flow | Handler/FSM evidence |
| --- | --- |
| Dashboard / start / menu | Dashboard counts, two progress bars, command interruption and one-message assertions |
| New task | Self/peer/shared target, editable draft after title, explicit creation, no record before save; duplicate submit produces one task |
| Collaborative ownership | Both users can view tasks; creator manages before first acceptance, personal executor or shared participants afterwards |
| Offer approval | Suggested price, custom final price, confirmed rejection, skip/next and configured approver permissions |
| Shared awards | Full amount per participant, one task/status, atomic dual award and original-week reversals |
| Templates | Shared catalog, default seeds, create/edit/disable, page recovery, immutable task snapshot and stale-template refresh |
| Reapproval and rerating | Semantic edits return to approval; current manual author can confirm old/new price before completion |
| Personal summaries | Mine/peer tabs show plan, pending count and completed task ledger total independently of list filters/pages |
| Quick navigation | Persistent six-button keyboard, slash aliases, leave/stay prompt, draft restoration and stale-control rejection |
| Deadline / calendar | Today/tomorrow/skip, valid date, Russian month names, disabled past dates, bounded month arrows |
| Planned effort / comment | Presets/custom/skip, peer visibility, direct comment input, edit/remove and Back |
| Pending ratings | Presets/custom, skip/wrap, advance from rated task, invalid/stale input, peer permissions |
| Task list / filters / pages | Five-item pages, selected filter, page counter and context through edits/status/confirmation |
| Deadline / overdue / manual penalty | Active lateness, fixed late completion, on-time/cancelled exclusions, scoped overdue filter, executor/comment prefills and exactly-once confirmation |
| Task details / start / done | Escaped full text, owner actions, automatic completion award and stale/duplicate callbacks |
| Cancel / reopen | Current confirmation nonce, cancelled confirmation cannot execute, score reversals |
| Penalty create / confirm | Target/type/presets/custom/comment/snapshots, no write before confirmation, duplicate prevention |
| Penalty deletion | Named amount preview, current confirmation, original-week reversal and list-page recovery |
| Results / weekly history | This/previous week, historical results, navigation and bounded pages |
| Penalty settings | Create/edit/method/rate/active toggle, one-tap enabling, description clearing and retained type page |
| Unexpected input | Unknown commands/media/choice-screen text retain prompt/controls; idle text reuses dashboard |
| Recovery | Deleted/uneditable menu replacement, malformed/old callbacks, incomplete FSM reset and dispatcher restart |
| Notification entry | Opens details in the clicked notification, retires old controls, and continues normal task actions |
| Telegram limits | Long escaped content fits 4096 parsed text units; callback payloads fit 64 UTF-8 bytes |
| Optional cleanup failure | Failed deleteMessage never rolls back a successfully saved task |

Tests include `tests/integration/test_collaborative_ui.py`, `test_business_collaborative_domain.py`,
`test_task_deadlines.py`, `test_mobile_ux.py`, `test_form_navigation.py`, `test_web_and_bot.py` and
`tests/unit/test_interaction.py`. These exercise the actual dispatcher and durable FSM; Telegram's
HTTP transport is mocked. The independent business gate must also remain green.

## Manual evidence and limitations

A local mobile harness (`tools/ux_preview.py`) was opened in Arc and visually inspected at 390 CSS
pixels. Dashboard, the title/deadline path, inline calendar, effort presets, direct comment entry and
the creation-result layout were inspected. Calendar controls and navigation fit the viewport.

This was **not** a Telegram production chat test. Automatic approval rejected access to native
Telegram. During the local inspection the Arc window became unavailable (`cgWindowNotFound`), and
Safari access was also rejected. Consequently, manual acceptance of every flow on an actual phone
remains unverified. The harness does not emulate native Telegram's font metrics, scrolling or keyboard.

## Reproduce the local preview

Use a separate disposable PostgreSQL database whose name ends in `_test`; the fixture drops its
application schema. The easiest isolated route uses the test Docker image:

```sh
docker build --target test -t taskflow:ux-test .
docker run --rm --name taskflow-ux-preview -p 127.0.0.1:18080:18080 \
  taskflow:ux-test sh -c 'runuser -u postgres -- /usr/lib/postgresql/15/bin/pg_ctl -D /tmp/taskflow-pg -l /tmp/taskflow-pg.log -o "-h 127.0.0.1" start && TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/taskflow_test python -m tools.ux_preview'
```

Open http://127.0.0.1:18080 and switch Max/Vika using the header. Both accounts and all records are test
fixtures; no production credentials or Telegram calls are used. Do not expose this developer server
publicly. Stop it with `docker stop taskflow-ux-preview`.

## Real-phone acceptance sequence

1. Max: `/start` → create a self task with a suggested price; inspect the draft, calendar,
   effort and comment. Verify no task appears before explicit creation. Vika accepts the price.
2. Max: create a task for Vika. Vika changes the final price in Inbox. Inspect the same task and
   current status from both accounts, then check personal totals with different list filters/pages.
3. Create a shared task, approve it and complete it from either account. Both receive the full
   amount once. Reopen with confirmation and check both original-week awards are reversed.
4. Use both default templates. Create/edit/disable a custom template; try a stale selected version
   and verify manually entered draft fields survive refresh. Peer template tasks still need acceptance.
5. The current manual rater changes a TODO and an IN_PROGRESS price with old/new confirmation.
   Check status is preserved and no points are awarded early. A title/effort edit requires fresh approval;
   a deadline/comment edit preserves approval. Completed tasks require reopening before editing.
6. In task, template, penalty and settings forms, use every bottom shortcut and slash alias.
   Stay restores input and fresh controls; Leave discards it. `/menu` and `/cancel` discard directly.
   Retry older confirmation buttons, restart the bot, delete the menu message and repeat navigation.
7. Vika: create a penalty for Max with custom minutes/comment, inspect the preview, then confirm.
   Change the penalty type and ensure old penalties retain their immutable calculation snapshots.
8. Both: inspect weekly results/history, reopening and penalty deletion across a week boundary.
   Repeated taps must not duplicate tasks, approvals, awards or penalties.

The collaborative extension still requires this actual-phone acceptance sequence before production
release. Automated dispatcher tests use mocked Telegram transport and real disposable PostgreSQL;
they cannot establish native Telegram keyboard appearance, scrolling or font metrics.

## Deadline extension visual inspection — 2026-10-07

The updated local mobile harness was inspected in Safari at its390 px phone layout using a
separate disposable PostgreSQL database and synthetic Max/Vika fixtures. Checked peer overdue
list, simultaneous estimate/lateness labels, completed-late card, manual penalty type pagination,
prefilled recipient/comment, amount selection, confirmation and successful isolated submission.
Text and controls fit the layout and remain readable. This is visual evidence for the harness;
it does not substitute for native Telegram rendering or a genuine two-account production flow.
