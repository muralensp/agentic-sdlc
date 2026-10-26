---
key: ADLC-018
type: feature
milestone: M2
priority: 18
depends_on: [ADLC-016]
adr: none
requires_approval: false
---
# ADLC-018: Run logs, usage measurement, `--dry-run`, `adlc loop-status`

**Description:** The execution log for every run, measured by the orchestrator.

**Linked PRD:** FR-18, FR-9h, FR-21

**Acceptance criteria:**
- `.adlc/logs/<KEY>/<run-n>/` has prompts, plan, per-iteration diffs, every command with exit code and output, files touched, tokens and elapsed time
- Times and tokens come from timestamps and API usage, never from agent text
- `--dry-run` shows task, branch and prompts without calling a model or touching git
