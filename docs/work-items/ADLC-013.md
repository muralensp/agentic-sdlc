---
key: ADLC-013
type: feature
milestone: M2
priority: 13
depends_on: [ADLC-006]
adr: none
requires_approval: false
---
# ADLC-013: Git worktree handling, branch naming, Conventional Commit check

**Description:** One worktree per task on `<type>/<KEY>-<slug>`, created from an up-to-date base branch.

**Linked PRD:** FR-7, FR-10b; decision 3

**Acceptance criteria:**
- Branch names follow the BOSS convention (including slug-only types)
- A commit message not matching `type(scope): subject` is rejected
- The primary checkout's code is never modified; only the board file and `.adlc/logs/` are written there
- Worktree and branch cleanup tested
