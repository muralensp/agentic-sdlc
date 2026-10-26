# Pending rows for BOSS NextGen's `docs/WORK-ITEMS.md`

**Not applied.** These rows are staged here. BOSS NextGen's `CLAUDE.md` says Phase 1 waits for explicit instruction and `main` is protected, so they are added there only when the user says so, on a branch and PR (`docs/adlc-adoption-items`), per its `CONTRIBUTING.md`.

Checked on 2026-10-26: the last existing key is `PLAT-007`, so these take `PLAT-008` to `PLAT-010`; the last existing ADR is `0049`, so the adoption ADR takes `0050`.

```
| PLAT-008 | Platform (Tooling) | ADLC adoption PR: `.adlc/config.yaml` extending the `dotnet` profile, `.adlc/AGENTS.md`, `docs/work-items/` brief template, `GLOSSARY.md` if missing, ADR-0050, one-line pointer in `CLAUDE.md`, `.gitignore` entry for `.adlc/logs/` | Open | ADLC PRD FR-29 / ADLC-024 | Produced by `adlc init` (dry-run, confirm, PR). All files are protected areas, so a human reviews and merges. Depends on ADLC-024. |
| PLAT-009 | Platform (Tooling) | Confirm every DoD script runs from Git Bash on this machine with usable exit codes (`lint.sh`, `test.sh`, `integration-test.sh`, `dependency-scan.sh`, `secret-scan.sh`, `smoke-test.sh`, `e2e-smoke-test.sh`, `contract-test.sh`) and record the baseline | Open | ADLC PRD 13.1 / ADLC-011, ADLC-014 | Needs Docker for secret scan and smoke tests. Known RabbitMQ first-boot race: a plain second `up` is the recovery (ADR-0028). Result goes in an execution log. |
| PLAT-010 | Platform (Tooling) | ADLC acceptance pilot: one low-risk task to a merged PR with no human step, plus one task built to fail that ends in `NEEDS_HUMAN` | Open | ADLC PRD Sec 11 / ADLC-029 | Depends on PLAT-008, PLAT-009 and ADLC-021, ADLC-027. Pick a task that touches no protected area. |
```

## Order across both boards

ADLC-001 to ADLC-007 (foundations and board), then PLAT-009 (verify scripts) alongside ADLC-008 to ADLC-011, then ADLC-012 to ADLC-023 (loop, review, escalation, delivery), then ADLC-024 to ADLC-026 (`init`, `new`, trust), then PLAT-008 (adoption PR, produced by `adlc init`), ADLC-027 and ADLC-028 (hardening), PLAT-010 with ADLC-029 (pilot). ADLC-030 (regenerate the docx) can be done any time. ADLC-031 (parallel work) stays deferred.
