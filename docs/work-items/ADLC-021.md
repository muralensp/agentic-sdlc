---
key: ADLC-021
type: feature
milestone: M3
priority: 21
depends_on: [ADLC-018, ADLC-020]
adr: none
requires_approval: false
---
# ADLC-021: PR step: push, PR body, template fill, auto-merge, cleanup

**Description:** Open the PR using the repo's template and the `pr` method; merge only when all conditions hold.

**Linked PRD:** FR-11, FR-12, FR-12a; `pr`

**Acceptance criteria:**
- PR body links the requirement, shows impacted modules, tests, risks and the gate checklist, with log links
- Auto-merge requires Reviewer approval, all DoD green, required remote checks green and no open trigger, then squash or rebase and cleanup
- A red check, missing approval or open trigger prevents merge (tests for each)
- Nothing is deployed

**Re-scope (decision 21):** The PR text is written by the pinned `pr` skill via the PR-writer role; the driver does push, `gh pr create`, check polling and auto-merge.
