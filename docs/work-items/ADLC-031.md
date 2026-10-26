---
key: ADLC-031
type: spike
milestone: M5
priority: 31
depends_on: [ADLC-029]
adr: none
requires_approval: true
---
# ADLC-031: Parallel workers (deferred, not scheduled)

**Description:** Only after a timed comparison of one task done sequentially versus in parallel shows a real gain.

**Linked PRD:** 7.2; M5

**Acceptance criteria:**
- Timed sequential-versus-parallel comparison recorded first
- If adopted: ownership table, per-worker runtime slots, `max_parallel`, orchestrator-owned integration
