# Telegram mobile UX implementation

This phase builds on the existing aiogram routers and persistent PostgreSQL FSM. Business rules and
migrations remain in the tested foundation layer; handlers coordinate input and screens only.

## Design

- One edited menu per private chat. Repeated commands, unexpected input and invalid buttons reuse it.
  A deleted/uneditable menu is replaced once. Notification messages remain separate entry points.
- Dashboard → task creation offers a one-tap finish after the title when details are unnecessary.
  Deadline/calendar, planned effort and comments remain optional. Comments can be typed directly.
- Task lists retain filter/page through details, editing, status changes and confirmations. Selected
  filters are visible; five-item pages use short labels with ellipses and readable full details.
- Rating presets act immediately, custom input keeps the task context, and successful rating advances
  from that task through the queue. Skipping wraps without losing pending work.
- Cancellation/reopening, penalty deletion and disabling a penalty type use current FSM confirmation
  tokens. Leaving or cancelling invalidates the old confirmation even if entity version is unchanged.
- `/menu` cancels any form. Other unexpected commands/media show guidance with current controls.
  Text on a choice screen keeps the draft intact. Old form buttons cannot update another draft.
- Calendar uses Russian month labels, disables past dates and bounds navigation. User timezone controls
  selected deadline dates; competition scoring timezone remains unchanged.
- Results/history and penalty settings retain their parent navigation. Settings use readable method
  labels; existing penalty snapshots are unaffected.

## Acceptance

Handler/FSM tests use actual aiogram dispatch, PostgreSQL storage and mocked Bot API. Cover all 21
requested capabilities, complete peer flows, interrupted/restarted forms, expired confirmations,
unexpected text/media/commands, deleted menu recovery, pagination/context, long escaped text and
message/callback limits. Business tests must stay green.

Manual acceptance uses an authorized Telegram client when accessible. Tool approval currently
rejected control of native Telegram; never report automated mocked flows as real chat/mobile tests.
Record exact manual evidence and remaining inaccessible scenarios in the UX report.
