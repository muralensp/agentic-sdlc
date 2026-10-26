---
key: ADLC-003
type: feature
milestone: M0
priority: 3
depends_on: [ADLC-001]
adr: none
requires_approval: false
---
# ADLC-003: Method registry and `adlc doctor`: pinned skill version and hash check

**Description:** Record which skills (mattpocock-skills set and project-local skills) each stage uses, pinned by version and content hash, and verify them.

**Linked PRD:** FR-31, FR-33, FR-34; decision 17

**Acceptance criteria:**
- `config.yaml: methods:` maps each stage in 7.3 to a skill name and pinned version/hash
- `adlc doctor` reports each method as available, missing or hash-mismatched, and which would use the fallback prompt
- Changing a pinned hash makes `doctor` fail (a test proves it can fail)
