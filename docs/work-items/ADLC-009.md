---
key: ADLC-009
type: feature
milestone: M2a
priority: 9
depends_on: [ADLC-003, ADLC-006]
adr: none
requires_approval: false
---
# ADLC-009: Method injection per stage, fallback prompts, method/version logging

**Description:** Tell the agent which method applies at the current stage and record it; fall back to `.adlc/methods/<name>.md` when the skill is unavailable or the runner is not Claude Code.

**Linked PRD:** FR-31, FR-32, FR-34

**Acceptance criteria:**
- Each stage in 7.3 resolves to a method or its fallback
- The run log records method name and version for every stage
- A skill cannot widen `AGENTS.md` permissions (a test proves a skill-suggested out-of-scope edit is refused)
