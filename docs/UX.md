# Mobile forms and time planning

For collaborative tasks, templates, complete participant lists, price changes and persistent
navigation, see [PRODUCT.md](PRODUCT.md). The task wizard described below is historical; the current
creation flow is target → title → editable draft card → explicit Create. Deadline/effort/comment
editors retain the calendar, presets, escaping and generation guards described here.

## Navigation

Task creation runs through title → deadline → effort estimate → optional comment.
Every later step has Back; returning preserves the draft and offers a keep button
where the previous value would otherwise need retyping. The calendar returns to
the deadline choices. Task field editors return to the edit menu; custom rating
returns to the same pending task.

Penalty creation runs through target → type → quantity → comment → confirmation.
Back from confirmation returns to the comment step; Back from quantity returns to
types, and Back from types returns to people. FIXED types skip quantity in both
directions. Selecting a different person/type clears the old preview/comment.
A penalty is persisted only by the current confirmation button.

Each rendered form step receives a fresh callback generation. Buttons from an
older visit cannot submit a revised draft. The operation key stays unchanged
throughout the draft so retries cannot create duplicates.

Invalid text shows a safe warning above the original prompt while retaining its
buttons. Warnings do not accumulate. One menu message is edited throughout.

## Quick choices

- Penalty minutes: 5, 15, 30, 60, 90 and 120; arbitrary integer 1–10,000 remains available.
- Manual penalty amount: 100, 500 and 1,000; confirmation remains mandatory.
- Task effort: 5, 15, 30 minutes, 1, 2 and 4 hours; custom input is an integer number
  of minutes, 1–10,080. “Не указывать” skips or clears an estimate.
- Weekly target: 5,000, 10,000, 15,000 and 20,000; custom target remains available.
- Display timezone: Moscow, Helsinki and UTC; any valid IANA name can be typed.
  Current UTC offsets are computed with ZoneInfo, including daylight saving time.

Timezone/target changes return to Settings. A display timezone changes date
presentation and deadline selection; the shared scoring timezone remains ENV-driven.
Penalty type creation supports Back, skipping its optional description and rate presets.

## Estimates

`tasks.estimated_minutes` is nullable and checked in both service and PostgreSQL.
An estimate describes planned effort, not elapsed time or a deadline. It appears
in task lists, details, creation confirmation and the peer rating screen. Only the
owner may edit it on an active task; changes are versioned and audited. Estimates
never affect rating, awards, penalties or weekly totals.

Alembic revision `0002` adds the nullable column without a backfill. Existing tasks
remain unestimated. Rolling back application code leaves this additive column and
its data intact; destructive schema downgrade is deliberately refused.

## Verification

`tests/integration/test_form_navigation.py` drives actual aiogram handlers against
PostgreSQL with mocked Telegram. It covers backward traversal, draft preservation,
presets, invalid input recovery, stale confirmation, estimate editing/removal,
permissions, audit snapshots and upgrading a populated revision-0001 schema.
Run `docker build --target test -t taskflow:test .` or `make check` with a disposable
`TEST_DATABASE_URL` ending in `_test`.

## Current navigation and recovery

After a title, “Создать без деталей” finishes in one tap. Comments may be typed directly on the
optional-comment screen. Task filters/pages survive editing and status changes. Active filters and
page totals are visible; long labels use ellipses. Five full-width task buttons carry status, points
and planned effort (or deadline); the task titles are not repeated in the message body. The screen
also totals planned effort for its page, and filters occupy two rows. The rating queue advances from the task just rated.

Reopen/cancel, penalty deletion and type disabling require a current FSM confirmation nonce. Leaving
the confirmation invalidates it even when entity version has not changed. Enabling a disabled type
needs one tap. Comment/description editors offer inline removal buttons.

Unknown commands, media and text on choice screens retain current controls. `/menu` exits a form.
Missing/corrupt draft data restores the dashboard. Non-navigation callbacks from old message IDs are
rejected. Deleted/uneditable UI messages are replaced; identical text uses `edit_reply_markup`.
Optional input-message cleanup cannot undo saved business actions if Telegram/network fails.

See [UX_ACCEPTANCE.md](UX_ACCEPTANCE.md) for the automated matrix, local mobile harness and exact
remaining real-phone acceptance limitations.

Notification and old-menu navigation adopts the clicked message as the current UI and retires the
previous keyboard. This keeps the opened screen where the user is looking, without sending another
message. Mutating callbacks from the retired message are rejected. A mutation must also match a button on the
current keyboard, so an old cached “Complete” cannot execute after returning to the dashboard.

## Collaborative UI acceptance

Persistent quick actions: Menu/New, Tasks/Inbox, Templates/Results. Native commands mirror them.
Menu/cancel discard directly; other quick transitions confirm before discarding an unfinished form.
Stay restores draft data and valid controls. Quick-button text is reserved navigation, never a title
or comment. Bottom keyboard installation is separate from the edited inline screen.

Task scope/filter/page survives viewing, edits and status actions. Everyone can inspect cards;
visible mutation buttons follow service permissions. Shared prices always say “each”, completion
says “Done for us”, and reopen confirmation explains both reversals. User summaries cover the
whole participant dataset regardless of page/filter and distinguish planned from earned points.

Price changes show the previous/new amount and require current nonce/version confirmation.
Automatic template pricing is labelled, and stale template selections require explicit resubmission.

## Deadline visibility and manual penalties

The overdue filter includes unfinished past-deadline tasks and tasks completed after their
deadline, excluding cancelled tasks. Mine/peer/shared/assigned scopes and five-item pagination
continue to apply; personal totals remain independent of the selected filter. Deadline and
planned duration are both visible in list rows. Cards show the deadline in the viewer’s timezone,
elapsed delay or fixed completion lateness, and Refresh. Date-only entry uses the final microsecond
of the selected local day. Completed-on-time tasks do not become overdue later.

Assign penalty opens the existing manual, explicitly confirmed form. A personal task preselects
its executor; a shared task asks which participant receives the penalty. The suggested editable
comment contains task ID/title, deadline and observed lateness. Type and amount are chosen manually;
no automatic penalty or award change occurs when a deadline passes. Repeated confirmation remains
operation-key protected. Template deletion is soft deletion in revision 0006 so existing task snapshots and audit history remain intact.
