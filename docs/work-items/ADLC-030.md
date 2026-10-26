---
key: ADLC-030
type: docs
milestone: M4
priority: 30
depends_on: []
adr: none
requires_approval: false
---
# ADLC-030: Regenerate the PRD `.docx` from `PRD.md`

**Description:** Retire the old docx snapshot by exporting the canonical Markdown.

**Linked PRD:** Decision 10

**Acceptance criteria:**
- `PRD_ageinticSDLC.docx` is replaced by an export of the current `PRD.md`
- The export keeps tables and headings
