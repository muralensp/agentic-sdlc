---
key: ADLC-014
type: feature
milestone: M2
priority: 14
depends_on: [ADLC-006, ADLC-011]
adr: none
requires_approval: false
---
# ADLC-014: DoD command runner: unpiped exit codes, saved output, baseline comparison

**Description:** Run each profile command itself and capture the real exit code; never trust an agent's claim.

**Linked PRD:** FR-10, FR-10a

**Acceptance criteria:**
- Each command runs without a pipe and its exit code is stored with the full output in the run log
- A command that exits non-zero but prints success is recorded as failed (test)
- Windows Git Bash execution of `bash build/scripts/*.sh` works
