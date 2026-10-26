---
key: ADLC-016
type: feature
milestone: M2
priority: 16
depends_on: [ADLC-009, ADLC-012, ADLC-013, ADLC-014, ADLC-015]
adr: none
requires_approval: false
---
# ADLC-016: Ralph loop controller

**Description:** Repeat fresh-context iterations with carried state until the orchestrator's completion check passes or a stop condition fires.

**Linked PRD:** FR-9, FR-9a to FR-9h; 8.2a

**Acceptance criteria:**
- Carried state is the branch diff, `progress.md` and trimmed last verification output; no conversation history
- Completion needs every DoD command to exit 0 and Reviewer approval; an agent's completion promise only triggers the check
- Stop conditions each have a test: max iterations, max minutes, same failure twice, regression (iteration reverted), guardrail violation
- Usage-window errors pause rather than count as an iteration (hook for ADLC-022)
