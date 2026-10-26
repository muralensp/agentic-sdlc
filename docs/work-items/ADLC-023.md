---
key: ADLC-023
type: feature
milestone: M3
priority: 23
depends_on: [ADLC-016]
adr: none
requires_approval: false
---
# ADLC-023: Spike tasks: `research` and `prototype`

**Description:** Support briefs of type `spike` that gather facts or answer a design question without producing mergeable code.

**Linked PRD:** FR-35

**Acceptance criteria:**
- `research` writes `docs/research/<topic>.md` with sources and no code changes
- `prototype` works on a `spike/` branch, records the answer, and the branch is deleted, never merged
- A task that depends on the spike unblocks when it is `DONE`
