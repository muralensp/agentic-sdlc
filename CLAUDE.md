# CLAUDE.md

Always-loaded context for **ADLC**, a local file-based agentic SDLC tool (Python). Keep this file short:
detail belongs in `PRD.md`, `docs/WORK-ITEM-WALKTHROUGH.md`, and the run logs. Maximum 150 lines.

## What this repo is

- The ADLC tool itself. It is installed on a machine (`pip install -e`) and operates on target repos; no ADLC
  code is ever copied into a target. BOSS NextGen (`C:\Murale\Projects\CustomsCleranceNextGenApp`) is the
  first and reference target. Design: `PRD.md` (canonical). Plan: `docs/WORK-ITEMS.md` plus briefs in
  `docs/work-items/`, explained in `docs/WORK-ITEM-WALKTHROUGH.md`.

## Working agreements

- Work one item at a time, in dependency order. Do not start the next item without an explicit instruction.
- Tests first (red, then green). Show the new tests fail before writing the code.
- Nothing is done until the checks have been run with real, unpiped exit codes and the output read.
- Commit and push only when the user asks. Never commit to `main` once it is protected (ADLC-002);
  use `<type>/<KEY>-<slug>` branches and a PR.
- Conventional Commits: `type(scope): subject`.
- Do not touch BOSS NextGen without explicit instruction (its `CLAUDE.md` says Phase 1 waits for it).

## Commands

```bash
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash; .venv/bin/python elsewhere
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m pytest
```

## Architecture choices (one line each; the PRD has the reasoning)

- A deterministic orchestrator owns the board, git and the state machine. LLM agents only do judgment work
  (tests, code, review, PR text) and never edit the board.
- Board format: BOSS NextGen's `WORK-ITEMS.md` table plus a brief per item. Only the Status and Notes
  cells are edited by the tool.
- Repo- and stack-specific behaviour lives in profiles and `config.yaml`, not in code.
- The fix loop is a Ralph loop: fresh context each iteration, progress carried in files and git, completion
  decided by orchestrator-run commands, never by the agent.
- Escalate to a human only on a defined trigger. Nothing is deployed in v1.

## Standing lessons (carried over from BOSS NextGen; apply proactively)

- Never trust a piped command's exit code (`cmd | tail` reports `tail`'s status). Capture it directly.
- Agent self-reported durations were 2 to 4 times the real ones: verify from outputs, not narrative.
- Prove a test can fail (fault injection), not just that it passes.
- A local pass can hide a timing race: poll, do not read logs once.
- Windows worktrees check out CRLF files; `.gitattributes` forces LF.
- Minimal cell edits only: never rewrite a whole table or file to change one value.

## Status

ADLC-001 to ADLC-021 and ADLC-032 are DONE (scaffold, branch protection, doctor, board, state machine, config, CLI, profiles, method injection, refine, baseline, agent runner, git worktrees, DoD verify, Tester red phase, Ralph loop, diagnosis iteration, run logs, Reviewer, escalation, PR step); `main` is protected. Next: ADLC-022. `docs/WORK-ITEMS.md` is the source of truth for status.
