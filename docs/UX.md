# Mobile forms and time planning

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
