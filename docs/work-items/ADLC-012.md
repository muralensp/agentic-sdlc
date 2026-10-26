---
key: ADLC-012
type: feature
milestone: M2
priority: 12
depends_on: [ADLC-006]
adr: none
requires_approval: false
---
# ADLC-012: Agent-runner interface, Claude Code adapter, fake client

**Description:** One interface for invoking an agent with a role, a model and instructions; a fake for tests.

**Linked PRD:** FR-22; Sec 9 Testability

**Acceptance criteria:**
- The orchestrator's tests run end to end with the fake client and no network
- The Claude Code adapter selects the model per role (Coder, Tester, Reviewer, PR writer) and refuses a Reviewer model equal to the Coder's
- Rate-limit errors from the adapter are surfaced as a distinct signal (used by ADLC-022)
