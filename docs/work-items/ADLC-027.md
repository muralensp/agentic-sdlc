---
key: ADLC-027
type: feature
milestone: M4
priority: 27
depends_on: [ADLC-016]
adr: none
requires_approval: true
---
# ADLC-027: Guardrail enforcement and secret/PII scrubbing

**Description:** Make `AGENTS.md` rules binding in code and keep sensitive data out of prompts and logs.

**Linked PRD:** FR-15, FR-16, FR-17

**Acceptance criteria:**
- Edits outside allowed paths and forbidden commands are blocked by the orchestrator and escalate
- Secrets and configured PII patterns never appear in prompts or logs (tests with planted values)
- Agents run with no network beyond the model API
