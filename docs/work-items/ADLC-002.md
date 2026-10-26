---
key: ADLC-002
type: feature
milestone: M0
priority: 2
depends_on: [ADLC-001]
adr: none
requires_approval: true
---
# ADLC-002: Remote repository and branch protection via a wizard script

**Description:** Creating the remote, protecting `main` and adding required checks are human-only steps. Emit them as an interactive script rather than prose.

**Linked PRD:** FR-36, FR-41; M0

**Acceptance criteria:**
- `.adlc/wizards/protect-main.sh` walks the person through creating the remote, requiring PRs and the CI check, and disabling direct pushes
- The script is idempotent and prints what it will do before doing it
- After the person runs it, a direct push to `main` is rejected (shown once as evidence)
