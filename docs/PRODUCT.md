# Product and user flows

## Purpose and permissions

TaskFlow is a Russian-language private productivity bot for two configured Telegram accounts, Max
and Vika. Usernames are not credentials: only allowed numeric sender IDs in their matching private
chats gain access. Both see results and penalties; a third account or group chat is denied.

Tasks can be created for oneself, the other participant or both. Both can view every configured
participant's tasks and statuses. Before first acceptance the creator controls an offer; after
acceptance the personal executor controls it. Shared tasks are controlled by both. Approval rights
belong to the other participant relative to the current proposal author, independently of executor.
Either participant can assign penalties and maintain shared penalty types and task templates.

## Dashboard and navigation

The Telegram Mini App dashboard shows both weekly results, pending approvals and active tasks. It
provides task creation, editing, cancellation, approval, ratings, templates, penalties, settings and
score history in a responsive light/dark interface. The Telegram menu button opens the app. `/start`
sends a one-time launcher and clears any old persistent reply keyboard. Manually typed `/menu` and
`/cancel` still restore the legacy inline menu. Bot command suggestions and notification action
buttons are removed. Menu/cancel discard the current form. Other quick navigation during an unfinished form requires
Leave and discard / Stay, preserving inputs on Stay. Context actions remain inline in one edited
screen. Shared tasks also have a separate dashboard entry.

Task tabs are Mine, Peer, Shared and Assigned by me. Mine/Peer include shared work, marked explicitly.
Filters include All (default), Active, In progress, Pending agreement, Done and Archive. Lists have
five items per page and preserve tab/filter/page when returning from a card. The user-level summary
covers the entire list regardless of filter/page: TODO points, IN_PROGRESS points, combined plan,
pending count and all-time completed task ledger points excluding penalties. Shared awards count
once at full value per participant. Unapproved prices and cancelled work do not enter the plan.

## Create and edit a task

Choose Me / Peer / Both, enter title, then edit a draft card in any order: title, executor,
proposed points, deadline, effort and comment. Fields retain existing limits (title 1–200, points
1–10000, effort 1–10080 minutes, comment 1–1500). Points and all details except title are optional.
Only Create persists a task. Back preserves values, Menu/Cancel discard them. Deadlines use the
creator's display timezone and end at 23:59:59; dates before today or after 2100 are rejected.
Effort is planning information, never a score multiplier.

Ordinary self tasks need the peer's approval; peer-assigned tasks need the executor's acceptance;
shared tasks need the other creator's approval. An omitted price is supplied by the approver.
The approver may accept the proposal, choose a final different price, skip or confirm rejection.
Rejection cancels the task. Successful approval moves BACKLOG to TODO without earning points.

Changing title or effort after approval returns the task to BACKLOG, proposing its previous price
and requiring the other editor's approval. Deadline/comment edits retain approval. First acceptance
remains recorded so these edits never restore the original creator's management rights. DONE
requires confirmed reopen before editing; CANCELLED is terminal.

## Templates

Templates are shared and require no approval. Either participant creates, edits or disables them.
Defaults are English for 60 minutes / 500 points and Diploma for 60 minutes / 500 points, seeded
idempotently without overwriting edits. A template has a title, optional effort and mandatory price.
The catalog is paginated; a draft can also be saved as a template (deadline/comment are not copied).

A template fills a draft. Self/shared tasks with unchanged title/effort/price are immediately TODO;
peer-assigned tasks still require acceptance. Changing title, effort or price removes automatic
approval; deadline/comment do not. Each task snapshots the template and version. Template edits
only affect future tasks; stale or disabled selections require refreshed explicit submission.

## Rating and task states

Inbox combines rating and acceptance requests. Suggested price can be accepted with one button;
custom price uses the existing numeric limits/presets and becomes final without a counteroffer.
The current manual rating author can change TODO/IN_PROGRESS price after old/new confirmation.
Status is retained, the executor is notified, and no score event is created until completion.
The same value is a no-op. Automatic template prices have no manual rating author.

| From | Action | Result |
| --- | --- | --- |
| BACKLOG | Other proposal author approves | TODO |
| BACKLOG | Confirm rejection/cancellation | CANCELLED |
| TODO | Executor starts/completes/cancels | IN_PROGRESS / DONE / CANCELLED |
| IN_PROGRESS | Executor returns/completes/cancels | TODO / DONE / CANCELLED |
| TODO / IN_PROGRESS | Title/effort changes | BACKLOG awaiting other editor |
| DONE | Confirm reopen | TODO / IN_PROGRESS, original award reversed |
| CANCELLED | None | Terminal archive state |

A shared task has one status, one completion timestamp and an award per participant. Either may
Complete for us, atomically awarding full price to both. Confirmed reopen reverses both original
awards in their original week; completion again earns a new award in its new week.

## Epics, subtasks and linked tasks

Any top-level task may be used as an epic. Its manager can create new subtasks or attach an existing
root task; parent and child share the epic's executor choice. Each child still has its own proposal,
approval and reward, and attaching an existing child preserves its price and score history. Children
cannot contain children. The epic detail shows progress, the sum of agreed child prices and its own
separate bonus price. Pending proposals are excluded from the sum; a shared child's price is counted
once and the UI labels its per-person payout. The parent bonus follows the ordinary approval flow.

An epic can be completed only after all attached children are DONE. A cancelled child blocks the
epic until detached. To reopen a completed child, first reopen the epic; its bonus is reversed in its
original week, then the child can be reopened and its own award reversed. Reopening a blocker does
not reopen already completed dependent tasks.

Tasks can also be linked as RELATED or DEPENDS_ON. RELATED is informational. A dependency requires
the selected blocker to be DONE before the dependent task can be completed. Dependency cycles are
rejected. Linked tasks appear in details with blockers and their current statuses.

Task lists show top-level items with their subtasks inside epic details. All (the default) excludes
completed tasks; Done contains completed items and Archive contains cancelled items. Completing a
subtask updates epic progress without moving the parent into a completed state.

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
Approval notifies the proposal author; rerating notifies the executor (shared: the other actor); penalty creation notifies its recipient. Routine status changes do not spam.

Ambiguous sends/crashes may omit a notice rather than replay it; the menu remains the source of truth.
Both users must start the bot before Telegram permits notices. Render Free can sleep and delay updates.
No recurring reminder schedule or delivery guarantee is promised. See [NOTIFICATIONS.md](NOTIFICATIONS.md).

## Acceptance and scope

Follow [UX_ACCEPTANCE.md](UX_ACCEPTANCE.md) and [QA_REPORT.md](QA_REPORT.md): two real accounts must
create → rate → complete → reopen; verify penalties/snapshots, stale buttons, restart and redeploy
persistence. Mocked Bot API tests are separate evidence from real phone acceptance.
Groups, extra users, recurring tasks, attachments, automatic deadline penalties and a web dashboard
are outside the implemented product.

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
