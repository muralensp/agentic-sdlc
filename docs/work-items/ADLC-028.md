---
key: ADLC-028
type: feature
milestone: M4
priority: 28
depends_on: [ADLC-003, ADLC-027]
adr: none
requires_approval: true
---
# ADLC-028: Prompt-injection review, skill supply-chain checks, index-size warning

**Description:** Review the untrusted-input paths and the third-party skill path.

**Linked PRD:** FR-17, FR-33, FR-37

**Acceptance criteria:**
- A written review of injection via task text, repo content and skill text, with mitigations tested
- Changing a pinned skill version or hash is a protected-area change
- `adlc doctor` warns when an index file exceeds `max_index_lines`
