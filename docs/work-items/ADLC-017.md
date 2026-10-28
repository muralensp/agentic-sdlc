---
key: ADLC-017
type: feature
milestone: M2
priority: 17
depends_on: [ADLC-016]
adr: none
requires_approval: false
---
# ADLC-017: Diagnosis iteration for stuck loops and bugfix tasks

**Description:** When stuck detection fires, run exactly one diagnosis iteration before escalating; use the same method for `bugfix/` tasks.

**Linked PRD:** FR-32; `diagnosing-bugs`

**Acceptance criteria:**
- A stuck loop gets one diagnosis iteration and the report is attached to the escalation
- `bugfix` tasks start with a reproducing test
- No second diagnosis iteration is ever started for the same stuck signature

**Re-scope (decision 21):** The diagnosis iteration invokes the pinned `diagnosing-bugs` skill from the driver; no diagnosis logic in Python.
