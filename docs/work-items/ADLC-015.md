---
key: ADLC-015
type: feature
milestone: M2
priority: 15
depends_on: [ADLC-012, ADLC-014]
adr: none
requires_approval: false
---
# ADLC-015: Tester red phase and test protection

**Description:** Tests are written first from the acceptance criteria, shown to fail on unmodified code, then locked against the Coder.

**Linked PRD:** FR-8, FR-9g

**Acceptance criteria:**
- The orchestrator confirms the new tests fail before the Coder starts; tests that pass already are rejected
- A Coder edit or deletion of a Tester test is blocked and triggers escalation
- Uses the `tdd` method: one behaviour at a time

**Re-scope (decision 21):** The Tester is a subagent definition (ADLC-033). The driver still runs the red check and protects the test files; a hook blocks the Coder from editing them.
