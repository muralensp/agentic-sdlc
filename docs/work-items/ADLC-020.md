---
key: ADLC-020
type: feature
milestone: M2b
priority: 20
depends_on: [ADLC-016, ADLC-019]
adr: none
requires_approval: false
---
# ADLC-020: Escalation engine, `NEEDS_HUMAN` queue and approve/retry/edit/reject

**Description:** Evaluate every trigger after each iteration and before merge; stop and surface the task when one fires.

**Linked PRD:** FR-23 to FR-26, FR-27; 8.4a

**Acceptance criteria:**
- Every trigger in 8.4a has a test, including protected areas, size, quality regression, test integrity and spec problem
- `adlc retry --refine` reopens the grilling session for a spec-problem escalation
- No escalated task can merge without `adlc approve`
- `adlc report` shows rates by trigger and how often the person overrode the agents
