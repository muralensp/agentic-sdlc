---
key: ADLC-026
type: feature
milestone: M3b
priority: 26
depends_on: [ADLC-025, ADLC-021]
adr: none
requires_approval: true
---
# ADLC-026: Safe defaults for new repos and `adlc trust`

**Description:** Fresh projects have no safety net, so auto-merge stays off until earned.

**Linked PRD:** FR-41; decisions 18, 20

**Acceptance criteria:**
- `auto_merge: false` until the doctor baseline is green and three merges were approved by hand
- `adlc trust` flips the setting and the setting never flips itself (test)
- Default protected-area list for a repo with no profile match is applied
