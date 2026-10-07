# Product and user flows

## Purpose and permissions

TaskFlow is a Russian-language private productivity bot for two configured Telegram accounts, Max
and Vika. Usernames are not credentials: only allowed numeric sender IDs in their matching private
chats gain access. Both see results and penalties; a third account or group chat is denied.

Each creates tasks for themselves. The other participant alone supplies the rating. The owner edits
active task fields and changes task status. Either participant can assign a penalty to either user,
confirm its deletion, and edit shared penalty types. These are the implemented trust rules.

## Dashboard and navigation

`/start`, `/menu` and `/cancel` restore the dashboard and interrupt forms. Normal use is inline:
Add task, Penalty, Tasks, Rate, Results and Settings. Dashboard shows both weekly results/targets,
the sender's active-task count and pending peer-rating count. Empty queues show “Всё оценено”.

Prefer one edited message, five-item lists, Back, optional skips and numeric presets. Invalid text
keeps the current step/buttons; stale buttons cannot overwrite newer data. Deleted menu messages
are replaced. Unfinished valid forms persist across restart; opening the menu discards the draft.

## Create and edit a task

1. Enter a trimmed title, 1–200 characters.
2. Pick no deadline (one tap), today, tomorrow or a date in the inline calendar.
3. Skip effort estimate or choose 5/15/30/60/120/240 minutes; custom integer 1–10080 is allowed.
4. Skip comment or enter 1–1500 characters; submitting this final step creates the task.
5. Open tasks, create another, or return to menu.

Owner is the authenticated sender; evaluator is the other user; initial status is BACKLOG. No task
row is created by an incomplete/cancelled draft. Estimate is planning information, never a score
multiplier. Deadlines use the owner's personal timezone and end at 23:59:59 on the chosen date.
Calendar supports Monday-first months and leap days; no past date or year after 2100 is selectable.
Missing a deadline does not automatically create a penalty.

Owners can edit title, deadline, estimate and comment while BACKLOG/TODO/IN_PROGRESS. Clearing optional
fields is supported. Editing an already rated task retains its rating: changing its meaning requires
agreement between the two participants. No rerating or user-facing hard task deletion is implemented.

## Rating and task states

The rating queue displays one pending peer task. Presets are 100/250/500/750/1000; custom points must
be an integer 1–10000. Skip advances with wrap-around without changing status. Successful rating
records evaluator/time/points, moves BACKLOG → TODO, queues an owner notice and opens the next task.
Rating alone adds no points.

| From | Allowed action | Result |
| --- | --- | --- |
| BACKLOG | Peer rates | TODO |
| BACKLOG | Owner confirms cancellation | CANCELLED |
| TODO | Owner starts / completes / confirms cancellation | IN_PROGRESS / DONE / CANCELLED |
| IN_PROGRESS | Owner completes / confirms cancellation | DONE / CANCELLED |
| DONE | Owner confirms reopen | TODO or IN_PROGRESS, original award reversed |
| CANCELLED | None | Terminal archive state |

The service also supports IN_PROGRESS → TODO and confirmed DONE → CANCELLED; these two transitions
have no dedicated UI button. DONE can be reached without starting first. Task filters: active,
in progress, pending rating, completed, cancelled; lists preserve page/filter when returning from details.

## Weekly results and history

Default target is 10000 per user. Result = completed awarded task points − penalties; negative
results are valid. Progress bars clamp to 0–100%, remaining target to at least zero.
Completion snapshots rated points, with membership determined by `completed_at`, not task creation.
Reopening inserts a compensating event in the original completion week. Recompletion belongs to
its new completion week. CANCELLED contributes no completed award.

Weeks run Monday 00:00 to next Monday in shared `DEFAULT_TIMEZONE` (Europe/Helsinki by default),
using UTC half-open intervals and real calendar arithmetic across DST. Personal timezone settings
change display/deadline dates, not comparison boundaries. History is five weeks per page, computed
from immutable ledger events including reversals; it uses current targets and shared timezone.
Changing these preferences can change historical display; historical targets are not snapshotted.

## Create and remove penalties

Choose recipient → active penalty type → minutes or manual points when needed → optional comment →
preview → explicit confirmation. PER_MINUTE = minutes × rate; FIXED = rate; MANUAL = entered points.
Minutes must be 1–10000; final points 1–1000000000; non-manual rate 1–1000000.
Back/quick values reduce typing; cancellation creates nothing.

Seeded defaults: Instagram 100/min, TikTok 100/min, YouTube 10/min, lateness 50/min; missed task,
missed deadline and Other use manual amounts. They are DB configuration, not handler constants.
Every penalty snapshots name, description, method and rate, plus quantity/points/comment. Emoji is
not snapshotted. Editing a type never recalculates existing penalties. An edited/disabled type while
a confirmation is open invalidates that preview and requires reselection/confirmation.

The shared penalty list supports confirmed soft deletion; it preserves the row and adds a compensating
score event effective at the original penalty date. Either authorized user can perform this action.

## Settings

Both users can create/edit type name, emoji, description, method and rate, and confirm enable/disable.
Method/rate changes save together; entity versions reject old edits. Disabled types remain in settings
but cannot create new penalties. Changes apply without restart.

Personal settings include weekly target (1–1000000) and IANA timezone, with Moscow/Helsinki/UTC presets
and custom text. Startup preserves these preferences and edited defaults. Configured names/IDs sync
at startup; changing allowed IDs transfers access and requires deliberate operational review.

## Notifications and delivery limits

New rating tasks coalesce into one pending batch, default debounce five seconds. PostgreSQL allows
at most one rating notification attempt per hour per recipient. A batch inside cooldown waits until
eligible; an empty queue skips. The message shows the current pending count and opens the rating queue.
Rating notifies the owner; penalty creation notifies its recipient. Routine status changes do not spam.

Ambiguous sends/crashes may omit a notice rather than replay it; the menu remains the source of truth.
Both users must start the bot before Telegram permits notices. Render Free can sleep and delay updates.
No recurring reminder schedule or delivery guarantee is promised. See [NOTIFICATIONS.md](NOTIFICATIONS.md).

## Acceptance and scope

Follow [UX_ACCEPTANCE.md](UX_ACCEPTANCE.md) and [QA_REPORT.md](QA_REPORT.md): two real accounts must
create → rate → complete → reopen; verify penalties/snapshots, stale buttons, restart and redeploy
persistence. Mocked Bot API tests are separate evidence from real phone acceptance.
Groups, extra users, recurring tasks, attachments, automatic deadline penalties and a web dashboard
are outside the implemented product.
