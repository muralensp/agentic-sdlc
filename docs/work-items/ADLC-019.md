---
key: ADLC-019
type: feature
milestone: M2b
priority: 19
depends_on: [ADLC-016, ADLC-009]
adr: none
requires_approval: false
---
# ADLC-019: Reviewer agent: two-axis code review and gate checklists

**Description:** A separate agent on a different model reviews the diff on a Standards axis and a Spec axis and fills the applicable gate checklist.

**Linked PRD:** FR-32, 7.1 (gates); decision 8

**Acceptance criteria:**
- Returns approve, changes-requested or escalate with findings listed per axis
- Gates 1 to 3 per PR as confirmed; Gate 4 is Not Applicable until a frontend exists; Gate 5 is never run by ADLC
- Changes-requested feeds back into the loop for at most `max_review_rounds`
