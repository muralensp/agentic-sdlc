---
key: ADLC-032
type: spike
milestone: M2a
priority: 32
depends_on: [ADLC-001]
adr: none
requires_approval: false
---
# ADLC-032: Substrate prototype: `claude -p`, Stop hook, PreToolUse hook

**Description:** Prove the hybrid on one throwaway task before building on it: a driver-style `claude -p` call with a `Stop` hook that runs the DoD, and a `PreToolUse` hook that blocks a board edit.

**Linked PRD:** Decision 21

**Acceptance criteria:**
- A scripted `claude -p` call finishes only when the DoD command exits 0; with a failing test, the Stop hook blocks the stop (shown once)
- A `PreToolUse` hook refuses an edit to `docs/WORK-ITEMS.md` and to a protected path (shown once), and allows an edit elsewhere
- Findings (headless flags, hook payloads, usage-window signal, per-subagent model) recorded in `docs/research/claude-code-substrate.md`
