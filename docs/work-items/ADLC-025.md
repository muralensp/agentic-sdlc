---
key: ADLC-025
type: feature
milestone: M3b
priority: 25
depends_on: [ADLC-008, ADLC-002]
adr: none
requires_approval: true
---
# ADLC-025: `adlc new`: scaffold a fresh repository from templates

**Description:** Create a minimal, ready-to-use repo for the chosen stack.

**Linked PRD:** FR-29, FR-40; decision 12

**Acceptance criteria:**
- Produces `.gitattributes`, board, `docs/work-items/`, CLAUDE.md/AGENTS.md, GLOSSARY.md, ADR and PR templates, gate checklists, CI workflow, a first ADR for the stack choice, and `.adlc/`
- A repo created for each profile passes its own CI on its first PR
- Repo creation and branch protection are emitted as a wizard script, not performed silently
