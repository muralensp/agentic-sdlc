---
key: ADLC-022
type: feature
milestone: M3
priority: 22
depends_on: [ADLC-018, ADLC-020]
adr: none
requires_approval: false
---
# ADLC-022: Resume, reset, usage-window pause, handoff, report

**Description:** Recover cleanly from interruption and subscription limits.

**Linked PRD:** FR-13, FR-9i, FR-18b, FR-26

**Acceptance criteria:**
- `adlc resume` continues from carried state; `adlc reset` clears it
- A usage-window error marks the task `PAUSED` in Notes, counts no iteration and resumes after the window resets; `max_tasks_per_window` is honoured
- `adlc handoff` writes a short note linking log, branch and last failure, secrets redacted
