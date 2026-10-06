# Product and acceptance flows

## Access and menu

Only the configured Max and Vika Telegram IDs in private chats can use the app. Others receive a lock message without data. `/start` or `/menu` restores one edited menu with both weekly scores, pending ratings, active task counts and inline navigation.

## Tasks

Add → type title → no deadline/today/tomorrow/calendar → skip or enter comment. Sender owns the BACKLOG task; the other user rates it. Ratings offer presets or a custom integer 1–10000, skip and automatic next item. Rating alone earns nothing.

Tasks show five per page, with active, in-progress, pending, completed and archive filters. Owners can edit active task title/comment/deadline, start or complete rated work. Completion awards the rating snapshot. Reopening completed work and cancellation require confirmation. The restored active task earns zero until completed again. Old versioned buttons display safe feedback.

## Penalties and settings

Penalty → target person → type → minutes or manual amount → optional comment → preview → confirm. Either user may assign either person. No database penalty exists before confirmation. Penalty lists allow confirmed soft deletion. Type settings permit creation, text/emoji/description/rate/method edits and enable/disable without deployment. Existing penalty amounts never recalculate.

Each user can change their weekly target and display timezone. Shared week boundaries remain DEFAULT_TIMEZONE (Europe/Helsinki initially). Results show completed awards minus penalties, including negative totals; history lists past weeks with ledger activity range. Targets displayed for past weeks are current targets.

## Manual acceptance

1. Max and Vika press Start; a third account sees only the private-bot notice.
2. Max creates “Production acceptance” with no deadline or comment. Verify it awaits Vika's rating.
3. Vika rates 500. Verify Max's notification and TODO state; weekly score has not increased.
4. Max completes it. Verify +500 in the completion week.
5. Max creates ten tasks quickly. Verify at most one rating notification to Vika in one hour, while the menu count reflects all tasks.
6. Vika assigns Max 12 Instagram minutes and confirms. Verify −1200 and net −700 from the two acceptance actions.
7. Change Instagram rate to 150; the existing penalty remains1200, a new one-minute penalty is150. Cancelled preview creates nothing.
8. Max reopens the completed task with confirmation. Verify the original500 award is reversed and an audit event exists.
9. Redeploy; verify records, modified rate and any unfinished draft persist. Navigate task pages and use an old callback; no duplicate award/penalty or crash occurs.

Acceptance records are ordinary persistent records. Cancel tasks and soft-delete penalties through the UI when cleaning up; retain audit/ledger history.
