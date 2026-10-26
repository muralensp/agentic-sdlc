---
key: ADLC-008
type: feature
milestone: M2a
priority: 8
depends_on: [ADLC-006]
adr: none
requires_approval: false
---
# ADLC-008: Stack profiles: `dotnet` (from BOSS NextGen) and `python`

**Description:** Ship the two v1 profiles as config files, not code.

**Linked PRD:** FR-39; Sec 13.1, 13.2

**Acceptance criteria:**
- `profiles/dotnet.yaml` holds the BOSS NextGen DoD commands, protected areas, branch and commit conventions (13.1)
- `profiles/python.yaml` holds the 13.2 defaults
- A test loads each profile and checks every referenced command key exists
