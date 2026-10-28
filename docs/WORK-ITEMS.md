# Work Items Log

Task board for the ADLC tool itself, in the same table format as BOSS NextGen's `docs/WORK-ITEMS.md` (PRD FR-1). One row per work item, added before its branch. Each item has a brief in `docs/work-items/{KEY}.md` with front-matter and acceptance criteria (FR-2). Until ADLC can run its own tasks, items are done by hand with Claude Code, one at a time, in dependency order. `Open` maps to ADLC state `BACKLOG`.

Context code: `ADLC`. Milestones refer to PRD section 12.

| Key | Context | Title | Status | Linked PRD Chapter / Day | Notes |
|---|---|---|---|---|---|
| ADLC-001 | ADLC (Tool) | Repository scaffold: git init, pyproject, pytest, ruff, CI, CLAUDE.md, .gitattributes, GLOSSARY.md | DONE | Sec 9, 12 (M0); FR-37 | M0. Brief: docs/work-items/ADLC-001.md. Local checks green; CI passed on PR #1 (2026-10-28). |
| ADLC-002 | ADLC (Tool) | Remote repository and branch protection via a wizard script | DONE | FR-36, FR-41; M0 | M0. Brief: docs/work-items/ADLC-002.md. Wizard run on 2026-10-28; direct push to main rejected (GH006: changes must be made through a pull request; required check "CI passed" expected). Merged in PR #1. |
| ADLC-003 | ADLC (Tool) | Method registry and `adlc doctor`: pinned skill version and hash check | DONE | FR-31, FR-33, FR-34; decision 17 | M0. Brief: docs/work-items/ADLC-003.md Built and tested; doctor passes 12 of 12 against the installed mattpocock-skills 1.3.1. Awaiting PR merge. Merged in PR #7. |
| ADLC-004 | ADLC (Tool) | `WORK-ITEMS.md` table parser and writer (Status and Notes cells only) | DONE | FR-1, FR-4, FR-4a | M1. Brief: docs/work-items/ADLC-004.md. Merged in PR #3; BOSS NextGen's real board round-trips byte for byte (local check). |
| ADLC-005 | ADLC (Tool) | Brief validator and task state machine | DONE | FR-2, FR-3, FR-5 | M1. Brief: docs/work-items/ADLC-005.md Built and tested; all 33 briefs on this board validate. Merged in PR #8. |
| ADLC-006 | ADLC (Tool) | Config and profile loader (`config.yaml` extends a profile) | DONE | FR-39, FR-20, FR-22 | M1. Brief: docs/work-items/ADLC-006.md. Merged in PR #9; this repo's own config loads. |
| ADLC-007 | ADLC (Tool) | CLI skeleton: `adlc add`, `adlc status`, `adlc log` | DONE | FR-5, FR-19, FR-27 | M1. Brief: docs/work-items/ADLC-007.md. Merged in PR #10; a copy of BOSS NextGen's board gives PLAT-008 with one added line. |
| ADLC-008 | ADLC (Tool) | Stack profiles: `dotnet` (from BOSS NextGen) and `python` | IN_REVIEW | FR-39; Sec 13.1, 13.2 | M2a. Brief: docs/work-items/ADLC-008.md. Built and tested; dotnet and python profiles, config.missing_dod, BOSS script names checked locally. Awaiting PR merge. |
| ADLC-009 | ADLC (Tool) | Method injection per stage, fallback prompts, method/version logging | Open | FR-31, FR-32, FR-34 | M2a. Brief: docs/work-items/ADLC-009.md Re-scoped by decision 21. |
| ADLC-010 | ADLC (Tool) | `adlc refine`: grilling interview that produces a ready brief | Open | FR-30, FR-38; decision 19 | M2a. Brief: docs/work-items/ADLC-010.md Re-scoped by decision 21. |
| ADLC-011 | ADLC (Tool) | `adlc doctor` baseline: run the profile's DoD commands once on the unmodified repo | Open | FR-29 (last bullet) | M2a. Brief: docs/work-items/ADLC-011.md |
| ADLC-012 | ADLC (Tool) | Agent-runner interface, Claude Code adapter, fake client | Open | FR-22; Sec 9 Testability | M2. Brief: docs/work-items/ADLC-012.md Re-scoped by decision 21. |
| ADLC-013 | ADLC (Tool) | Git worktree handling, branch naming, Conventional Commit check | Open | FR-7, FR-10b; decision 3 | M2. Brief: docs/work-items/ADLC-013.md |
| ADLC-014 | ADLC (Tool) | DoD command runner: unpiped exit codes, saved output, baseline comparison | Open | FR-10, FR-10a | M2. Brief: docs/work-items/ADLC-014.md |
| ADLC-015 | ADLC (Tool) | Tester red phase and test protection | Open | FR-8, FR-9g | M2. Brief: docs/work-items/ADLC-015.md Re-scoped by decision 21. |
| ADLC-016 | ADLC (Tool) | Ralph loop controller | Open | FR-9, FR-9a to FR-9h; 8.2a | M2. Brief: docs/work-items/ADLC-016.md |
| ADLC-017 | ADLC (Tool) | Diagnosis iteration for stuck loops and bugfix tasks | Open | FR-32; `diagnosing-bugs` | M2. Brief: docs/work-items/ADLC-017.md Re-scoped by decision 21. |
| ADLC-018 | ADLC (Tool) | Run logs, usage measurement, `--dry-run`, `adlc loop-status` | Open | FR-18, FR-9h, FR-21 | M2. Brief: docs/work-items/ADLC-018.md |
| ADLC-019 | ADLC (Tool) | Reviewer agent: two-axis code review and gate checklists | Open | FR-32, 7.1 (gates); decision 8 | M2b. Brief: docs/work-items/ADLC-019.md Re-scoped by decision 21. |
| ADLC-020 | ADLC (Tool) | Escalation engine, `NEEDS_HUMAN` queue and approve/retry/edit/reject | Open | FR-23 to FR-26, FR-27; 8.4a | M2b. Brief: docs/work-items/ADLC-020.md |
| ADLC-021 | ADLC (Tool) | PR step: push, PR body, template fill, auto-merge, cleanup | Open | FR-11, FR-12, FR-12a; `pr` | M3. Brief: docs/work-items/ADLC-021.md Re-scoped by decision 21. |
| ADLC-022 | ADLC (Tool) | Resume, reset, usage-window pause, handoff, report | Open | FR-13, FR-9i, FR-18b, FR-26 | M3. Brief: docs/work-items/ADLC-022.md |
| ADLC-023 | ADLC (Tool) | Spike tasks: `research` and `prototype` | Open | FR-35 | M3. Brief: docs/work-items/ADLC-023.md |
| ADLC-024 | ADLC (Tool) | `adlc init`: adopt an existing repository | Open | FR-29, FR-39; decision 12 | M3b. Brief: docs/work-items/ADLC-024.md |
| ADLC-025 | ADLC (Tool) | `adlc new`: scaffold a fresh repository from templates | Open | FR-29, FR-40; decision 12 | M3b. Brief: docs/work-items/ADLC-025.md |
| ADLC-026 | ADLC (Tool) | Safe defaults for new repos and `adlc trust` | Open | FR-41; decisions 18, 20 | M3b. Brief: docs/work-items/ADLC-026.md |
| ADLC-027 | ADLC (Tool) | Guardrail enforcement and secret/PII scrubbing | Open | FR-15, FR-16, FR-17 | M4. Brief: docs/work-items/ADLC-027.md Re-scoped by decision 21. |
| ADLC-028 | ADLC (Tool) | Prompt-injection review, skill supply-chain checks, index-size warning | Open | FR-17, FR-33, FR-37 | M4. Brief: docs/work-items/ADLC-028.md |
| ADLC-029 | ADLC (Tool) | Acceptance pilot on BOSS NextGen | Open | Sec 11; 13.1 | M4. Brief: docs/work-items/ADLC-029.md |
| ADLC-030 | ADLC (Tool) | Regenerate the PRD `.docx` from `PRD.md` | Open | Decision 10 | M4. Brief: docs/work-items/ADLC-030.md |
| ADLC-031 | ADLC (Tool) | Parallel workers (deferred, not scheduled) | Open | 7.2; M5 | M5. Brief: docs/work-items/ADLC-031.md |
| ADLC-032 | ADLC (Tool) | Substrate prototype: `claude -p`, Stop hook, PreToolUse hook | DONE | Decision 21 | M2a. Brief: docs/work-items/ADLC-032.md. Added by decision 21. Findings: docs/research/claude-code-substrate.md. Hooks work for file tools; Bash bypasses them, so the driver must re-check the diff. Merged in PR #6. |
| ADLC-033 | ADLC (Tool) | Claude Code assets package: subagents, hooks, deny rules | Open | Decision 21; FR-15, FR-22 | M2a. Brief: docs/work-items/ADLC-033.md. Added by decision 21. |
