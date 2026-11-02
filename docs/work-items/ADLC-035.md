---
key: ADLC-035
type: bugfix
milestone: M4
priority: 35
depends_on: [ADLC-027, ADLC-034]
adr: none
requires_approval: true
---
# ADLC-035: Headless agents may edit files, within a profile-built allow list

**Description:** The first real `adlc run` (PLAT-011 on BOSS NextGen, 2026-11-01) escalated with "the
Tester wrote no tests". In `claude -p` nobody can approve a tool call, so Claude Code denied every
`Write` and the call still ended `ok` with an empty diff. The runner passes no permission mode and no
allowed tools to the Tester and the Coder, and the tests only used fakes, so this was never seen. The
probe that found it: a real `claude -p` in the worktree reported the denied `Write` in
`permission_denials`.

**Linked PRD:** FR-15, FR-21; decision 21

**Acceptance criteria:**
- `AgentRequest` carries a permission mode and `ClaudeCodeRunner` passes it as `--permission-mode`;
  a request without one adds nothing, as today
- `GuardedRunner` gives the Tester and the Coder `acceptEdits` plus an allow list built from the
  profile: each DoD command, read-only `git` (`status`, `diff`, `log`, `show`) and `git add` and
  `git commit`; anything else is not allowed. It never uses `bypassPermissions`
- The Reviewer and the PR writer are unchanged: read-only tools, no permission mode, no Bash
- A request that already names a permission mode or allowed tools keeps them
- The deny rules (network tools, forbidden commands) still go on every call and win over the allow list
- When the Tester finishes without changing any file and Claude Code reported denied tool calls, the
  escalation reason names them (the tool and the path or command) instead of only "wrote no tests"
- Tested with the fake runner for the arguments, the allow list and the escalation reason; one test
  that fails against the code before this item. A live smoke test, skipped unless `ADLC_LIVE=1`, runs a
  real `claude -p` in a scratch repository and requires it to create a file (about $0.07)

**Not in this item:** writing the agents' replies and denials to the run log and keeping the original
text of the Notes cell (ADLC-036); denials during the Coder's loop (ADLC-036); the pre-edit path guard
for BOSS NextGen (`adlc init --claude-assets`, ADLC-033).
