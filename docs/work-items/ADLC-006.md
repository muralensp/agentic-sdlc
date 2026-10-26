---
key: ADLC-006
type: feature
milestone: M1
priority: 6
depends_on: [ADLC-001]
adr: none
requires_approval: false
---
# ADLC-006: Config and profile loader (`config.yaml` extends a profile)

**Description:** Load `.adlc/config.yaml`, resolve `extends: <profile>`, apply overrides and validate against a schema.

**Linked PRD:** FR-39, FR-20, FR-22

**Acceptance criteria:**
- Unknown keys and bad types fail validation with the key path in the error
- A repo config can override any profile key, including DoD commands and protected areas
- Defaults match the PRD (`max_iterations: 5`, `max_review_rounds: 2`, `max_diff_lines: 400`, `max_task_minutes: 30`, `max_tasks_per_window: 3`, `auto_merge` per FR-41)
