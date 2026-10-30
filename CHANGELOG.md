# Changelog

Generated from git history by `.claude/skills/changelog/generate.py`.
Do not edit by hand; merge commits are omitted. Newest date first.

## 2026-10-30

### Features

- **review**: Reviewer agent with two-axis verdict and gate checklists (`494bd2f`)

### Documentation

- **changelog**: add CHANGELOG.md and a skill to regenerate it from git history (`ef952c9`)

## 2026-10-29

### Features

- **runlog**: run logs, measured usage, loop-status and run --dry-run (`ccd1fb5`)
- **loop**: one diagnosis iteration for stuck loops; bugfix starts with a repro test (`8172a90`)
- **loop**: Ralph loop controller with stop conditions and carried state (`a03b789`)
- **tdd**: red check, Tester scope and lock on the Tester's tests (`6607292`)
- **verify**: run the Definition of Done in a worktree and record the result (`61ce9d7`)
- **gitwork**: worktrees, branch names and commit check per task (ADLC-013) (`f7d9070`)
- **runner**: agent-runner interface, Claude Code adapter and fake (ADLC-012) (`8e27fb6`)
- **doctor**: baseline of the Definition of Done commands (ADLC-011) (`04ecb50`)
- **cli**: add `adlc refine`, the grilling session launcher (ADLC-010) (`308aca5`)
- **methods**: resolve and inject the method for each stage (ADLC-009) (`8274c8f`)
- **profiles**: add dotnet and python stack profiles (ADLC-008) (`9767e56`)

### Fixes

- **tests**: give the run log tests a git identity (`317d0ef`)
- **gitwork**: support Python 3.11 when deleting a leftover worktree folder (`ea2e49b`)

## 2026-10-28

### Features

- **cli**: add `adlc add`, `adlc status` and `adlc log` (ADLC-007) (`4c8126b`)
- **config**: add the config and profile loader (ADLC-006) (`f47af0b`)
- **tasks**: add the brief validator, state machine and task selection (ADLC-005) (`13fb3b1`)
- **methods**: add the method registry and adlc doctor (ADLC-003) (`11a2fbf`)
- **spike**: prove Stop and PreToolUse hooks under headless Claude Code (ADLC-032) (`622778e`)
- **board**: add the WORK-ITEMS.md table parser and atomic writer (ADLC-004) (`15744de`)
- **wizard**: create the remote as public by default (ADLC-002) (`6823614`)

### Documentation

- **prd**: record decision 21, the hybrid Claude Code substrate (`8e8b233`)
- **board**: mark ADLC-004 done and use Generator for the lock context manager (`05eebc0`)
- **board**: mark ADLC-001 and ADLC-002 done (`8afe179`)
- **board**: record the rejected direct push as ADLC-002 evidence (`5e9aef1`)

### Tests

- **wizard**: make the stub gh executable so the tests pass on Linux CI (`14d39bf`)

### Chores

- **repo**: stop tracking compiled bytecode in tests/__pycache__ (`cca05bc`)

### Other

- scrach folder ignored. (`ad75d0d`)

## 2026-10-27

### Features

- **wizard**: add protect-main script for remote and branch protection (ADLC-002) (`16a921d`)

## 2026-10-26

### Chores

- **repo**: scaffold the ADLC Python package and project docs (ADLC-001) (`303dfc9`)
