# Collaborative TaskFlow implementation

Approved conversation plan, 2026-10-07. Implement all stages, no deployment.

## Product contract
- Creation SELF (default), PEER, SHARED. SELF manual peer approves; PEER recipient accepts; SHARED other author approves. Optional proposed points; approver's amount final, no counteroffer. Reject with confirmation -> CANCELLED.
- Templates shared CRUD/disable, no approval, seeded English 60 min/500 and diploma 60 min/500 idempotently. Template SELF/SHARED ready immediately; PEER still needs acceptance. Snapshot template/version. Changes to title/effort/points in template draft remove autoapproval; deadline/comment do not. Stale/disabled template rejects create and refreshes draft for explicit resubmit. Save draft as template supports mandatory points input. Template changes affect future tasks only.
- One shared task/status. Either completes for both, full price each. Atomic two score events, no duplicates. Confirmed reopen reverses BOTH original-week awards. Completion snapshots price; ledger immutable.
- View all configured participants' tasks/statuses. Scopes mine/peer/shared/assigned; mine/peer include shared. Filters all(default), active, work, backlog, done, archive. 5/page. Whole-user summary regardless filters/pages: TODO sum, IN_PROGRESS sum, combined plan, pending count, net task ledger all-time completed points without penalties. Shared once/full amount per user.
- Task creator immutable, executors immutable after create. Before first acceptance creator may edit/withdraw; after acceptance personal executor alone controls even in reapproval; both control shared. Visibility grants no mutations. Track first acceptance.
- Semantic edit title/effort resets approved task to BACKLOG, prior price proposed, other editor approves. Clear rating/source/start; cannot complete before approval. Deadline/comment retain rating. DONE requires reopen first; CANCELLED terminal.
- Manual rating author may rerate TODO/IN_PROGRESS only, with explicit old/new confirmation, immediately, preserve status, notify executor (shared: other actor). Same amount no-op. Final rating author differs from proposal author. Auto template rating has no manual author.
- UX create: target -> title -> draft card arbitrary field editing (title, target, points, deadline calendar, duration presets, comment) -> explicit create. No task persisted before create. Back preserves values, menu/cancel discard. Success actual state.
- Main menu week results and Add, Templates, All tasks, Shared tasks, Inbox count, Results, Penalty, Settings. Inbox supports approve suggested/custom price, reject confirmation, skip and next.
- Persistent bottom keyboard: Menu/New; Tasks/Inbox; Templates/Results. Commands /menu,/new,/tasks,/inbox,/templates,/results, /cancel and /start retained. Menu/cancel discard directly; other quick transitions on unfinished ANY form ask leave+discard or stay, restore exact draft with fresh valid controls. Keyboard message initialization separate from inline screen; routine screens edited. Commands scoped allowed chats; failed registration not fatal.
- All mutations service permissions/transactions/row-version+operation locks, caller commits. Actor IDs derived configured two participants. Extend idempotency creator checks. Notification dedup by action version; requests coalesced existing <=1/hour attempt; no status spam. FSM PostgreSQL, stale message/visible callback/generation guarded, escape text.
- Migration 0005 after 0004; never edit earlier applied. Backfill legacy PERSONAL creator=owner, participant owner, rating provenance, first acceptance. Preserve score/audit/history. Update constraints/new indexes. No destructive downgrade or old-code mixed workers after new data. Update AGENTS/business/product/UX/data docs.

## Stages and verification
1. Schema + repositories + domain services + templates + domain tests and migration tests.
2. UI/FSM draft/inbox/templates/tasks summaries and functional tests.
3. Persistent navigation + commands + interruption confirmation and tests.
4. Full review, lint/typecheck/unit + actual PostgreSQL integration/migration tests; Docker if available. Skips not passes. Two-phone acceptance cannot be claimed from mocks.

Limits remain points 1..10000, effort 1..10080 minutes. No recurrence, variable award shares, reassignment, deployment. No Git repository is present; no branch/worktree/commits possible. Work in current project and preserve unrelated files.
