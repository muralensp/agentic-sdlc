---
key: ADLC-029
type: test
milestone: M4
priority: 29
depends_on: [ADLC-021, ADLC-024, ADLC-027]
adr: none
requires_approval: true
---
# ADLC-029: Acceptance pilot on BOSS NextGen

**Description:** Prove v1 on the reference target: one ordinary task to a merged PR, and one built to fail.

**Linked PRD:** Sec 11; 13.1

**Acceptance criteria:**
- One low-risk task goes from `BACKLOG` to a merged, closed PR with no human step, with orchestrator-captured exit codes for every DoD command
- One task built to fail ends in `NEEDS_HUMAN` with reason and log link visible on the board
- Metrics in section 11 are recorded and compared with targets
