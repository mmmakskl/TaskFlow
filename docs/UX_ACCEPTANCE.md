# Mobile UX acceptance

## Scope and implementation

All requested flows use aiogram routers, PostgreSQL FSM, inline keyboards and a single edited menu.
Existing business services still decide permissions, transitions, snapshots, points and idempotency.
No schema change is introduced by this UX phase.

| Flow | Handler/FSM evidence |
| --- | --- |
| Dashboard / start / menu | Dashboard counts, two progress bars, command interruption and one-message assertions |
| New task | Full wizard and fast finish after title; duplicate submit produces one task |
| Deadline / calendar | Today/tomorrow/skip, valid date, Russian month names, disabled past dates, bounded month arrows |
| Planned effort / comment | Presets/custom/skip, peer visibility, direct comment input, edit/remove and Back |
| Pending ratings | Presets/custom, skip/wrap, advance from rated task, invalid/stale input, peer permissions |
| Task list / filters / pages | Five-item pages, selected filter, page counter and context through edits/status/confirmation |
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

Tests: `tests/integration/test_mobile_ux.py`, `test_form_navigation.py`, `test_web_and_bot.py` and
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

1. Max: Start → new task; create one without details and one with a calendar deadline, estimate/comment.
2. Vika: open pending ratings, skip once, rate with a preset and a custom value. Confirm the next item.
3. Max: list/filter/page → details → start → complete; check earned points. Reopen, cancel confirmation,
   then confirm reopening; verify the old award disappears. Edit an active task and verify Back context.
4. Vika: penalty → Max → type → quick minutes → optional comment → review/confirm. Check negative score.
5. Both: results → previous week → history; settings → types → change rate/create/toggle. Old penalties
   retain their amounts; new previews use new settings.
6. While in each form, send unexpected text/media, press Back, use /menu, and try an old callback.
   Delete the bot menu and reopen it. Repeated taps must not duplicate records or awards.
