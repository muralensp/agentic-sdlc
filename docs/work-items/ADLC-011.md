---
key: ADLC-011
type: feature
milestone: M2a
priority: 11
depends_on: [ADLC-006, ADLC-008]
adr: none
requires_approval: false
---
# ADLC-011: `adlc doctor` baseline: run the profile's DoD commands once on the unmodified repo

**Description:** Know what already fails before any task runs, so only new failures count against a task.

**Linked PRD:** FR-29 (last bullet)

**Acceptance criteria:**
- Runs each DoD command, records exit code and output, and stores the baseline in `.adlc/logs/baseline/`
- A task run compares against the baseline: pre-existing failures do not block, new ones do
- Run against BOSS NextGen's checkout, results match a manual run
