---
key: ADLC-004
type: feature
milestone: M1
priority: 4
depends_on: [ADLC-001]
adr: none
requires_approval: false
---
# ADLC-004: `WORK-ITEMS.md` table parser and writer (Status and Notes cells only)

**Description:** Read the Key/Context/Title/Status/Linked/Notes table and update only Status and Notes, atomically, under a lock file.

**Linked PRD:** FR-1, FR-4, FR-4a

**Acceptance criteria:**
- Round-trips BOSS NextGen's real `docs/WORK-ITEMS.md` byte-for-byte when nothing changes
- Editing one row's Status changes only that cell (the diff shows one line)
- Writes are atomic (temp file + rename) and a second concurrent writer is refused
- Rows outside the table, and unknown columns, are preserved
