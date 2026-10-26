---
key: ADLC-007
type: feature
milestone: M1
priority: 7
depends_on: [ADLC-004, ADLC-005, ADLC-006]
adr: none
requires_approval: false
---
# ADLC-007: CLI skeleton: `adlc add`, `adlc status`, `adlc log`

**Description:** The user-facing commands for the board.

**Linked PRD:** FR-5, FR-19, FR-27

**Acceptance criteria:**
- `adlc add` allocates the next key for a context code and creates the row and a brief skeleton
- `adlc status` lists FAILED and NEEDS_HUMAN items first, with reason and log link
- Works on a copy of BOSS NextGen's board without altering other rows
