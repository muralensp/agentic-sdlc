---
key: ADLC-005
type: feature
milestone: M1
priority: 5
depends_on: [ADLC-004]
adr: none
requires_approval: false
---
# ADLC-005: Brief validator and task state machine

**Description:** Validate `docs/work-items/{KEY}.md` front-matter and enforce legal state transitions including DRAFT, BACKLOG, NEEDS_HUMAN, BLOCKED and FAILED.

**Linked PRD:** FR-2, FR-3, FR-5

**Acceptance criteria:**
- An item with no brief, no acceptance criteria or malformed front-matter is rejected with a clear error and never picked up
- Illegal transitions are rejected; every legal transition is tested
- Only `BACKLOG` items are selectable; dependencies must be `DONE`
- Keys are `{CODE}-{seq}` and never reused
