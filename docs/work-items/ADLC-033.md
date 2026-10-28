---
key: ADLC-033
type: feature
milestone: M2a
priority: 33
depends_on: [ADLC-032, ADLC-008]
adr: none
requires_approval: true
---
# ADLC-033: Claude Code assets package: subagents, hooks, deny rules

**Description:** The files a target repo receives so Claude Code itself enforces roles and guardrails: `.claude/agents/{tester,coder,reviewer,pr-writer}.md` with a model per role, hook scripts, and `permissions.deny` for the board and protected areas, generated from the profile.

**Linked PRD:** Decision 21; FR-15, FR-22

**Acceptance criteria:**
- `adlc init` and `adlc new` can write the assets from templates; the protected-area list comes from the profile
- The Reviewer's model differs from the Coder's, and `doctor` fails if they are equal
- A hook blocks an edit to the board and to each protected area (one test per rule, each shown failing first)
- The assets sit under protected paths, so changing them always escalates
