---
key: ADLC-024
type: feature
milestone: M3b
priority: 24
depends_on: [ADLC-006, ADLC-008, ADLC-011]
adr: none
requires_approval: true
---
# ADLC-024: `adlc init`: adopt an existing repository

**Description:** Inspect the repo, propose a config, show the file plan, and deliver additive files as a PR after confirmation.

**Linked PRD:** FR-29, FR-39; decision 12

**Acceptance criteria:**
- Dry-run by default; nothing written until the person confirms
- Detects stack, test and lint scripts, CI, default and protected branches, existing board, PR and ADR templates, CONTRIBUTING and CLAUDE.md
- On BOSS NextGen the proposed DoD commands match `CONTRIBUTING.md` with no hand edits
- Adds only `.adlc/`, `docs/work-items/`, a `GLOSSARY.md` if missing and a pointer in CLAUDE.md, via a branch and PR
