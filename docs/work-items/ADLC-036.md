---
key: ADLC-036
type: bugfix
milestone: M4
priority: 36
depends_on: [ADLC-035]
adr: none
requires_approval: true
---
# ADLC-036: Run logs hold the agents' replies; board updates keep the Notes text

**Description:** Two more defects from the first real run (PLAT-011, 2026-11-01). The run folder
`.adlc/logs/PLAT-011/run-1` was empty, so nothing showed what the Tester said or what Claude Code
denied. And escalating or resetting the task replaced the whole Notes cell, losing the original note.

**Linked PRD:** FR-19, FR-27; 8.2a

**Acceptance criteria:**
- Every agent call in `adlc run` (Tester, Coder, Reviewer, PR writer) leaves its prompt, scrubbed reply,
  status and `permission_denials` in the run folder, including a call that escalates the task
- A denied tool call during the Coder's loop is recorded in that iteration's log entry and named in the
  escalation reason when the loop then stops without progress
- Escalating, pausing or resetting a task keeps the existing Notes text and adds the new reason after it;
  only the Status and Notes cells change, as before
- Tested against the fake runner in a real git repository; a test shows an empty Notes cell, a
  one-line cell and a cell with a `|` in backticks all survive

**Not in this item:** the permission grant itself (ADLC-035).
