---
key: ADLC-010
type: feature
milestone: M2a
priority: 10
depends_on: [ADLC-005, ADLC-009]
adr: none
requires_approval: true
---
# ADLC-010: `adlc refine`: grilling interview that produces a ready brief

**Description:** Interactive, human-present command that sharpens an idea or a DRAFT item into a validated brief.

**Linked PRD:** FR-30, FR-38; decision 19

**Acceptance criteria:**
- Reads CLAUDE.md, ADRs, glossary and the board first and looks up facts itself
- Asks in numbered rounds with a recommended answer each, and ends only when the person confirms shared understanding
- Writes row and brief; the validator passes before the item becomes `BACKLOG`
- Proposes (never writes) ADR and glossary entries for the person to accept or reject

**Re-scope (decision 21):** `adlc refine` becomes a thin launcher for an interactive Claude Code session that runs the pinned grilling skill; the driver validates the brief it produces.
