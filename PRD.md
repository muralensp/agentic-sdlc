# PRD: Local File-Based Agentic SDLC (ADLC)

**Status:** Draft v2 (supersedes `PRD_ageinticSDLC.docx`, an older snapshot of this document; the docx is to be regenerated from this file)
**Owner:** Solo developer (single user)
**Last updated:** 2026-10-26
**Process reference:** the BOSS NextGen / CustomsClearanceNextGenApp project (`CONTRIBUTING.md`, `CLAUDE.md`, `docs/PARALLEL-WORK.md`, ADRs 0003/0035, `docs/quality-gates/`, the multi-agent pilot metrics report). It is a real solo human + AI project that has already run this way for 50 days. Section 7.1 lists what this PRD borrows from it.

## 1. Summary

ADLC is a lightweight, local tool that takes tasks from the repository's own Markdown work-item log (v1: BOSS NextGen's `docs/WORK-ITEMS.md`) and carries each one through implementation, testing, independent review, and a merged, closed pull request using LLM agents. It escalates to the human only when a defined trigger fires. It replaces Jira and other external project-management APIs with a plain-text task board that lives in the repository, so both the human and the agents can read and track state with no integration overhead. ADLC is a generic tool: it is installed on the machine and operates on a target repository, which can be an existing project or a freshly started one. BOSS NextGen (.NET 8) is the first and reference profile and the v1 acceptance target; ADLC itself is a Python package. Each stage of the lifecycle uses a proven working method, packaged as a skill (grilling, TDD, diagnosis, code review and others; section 7.3).

## 2. Problem

- Enterprise tools like Jira add friction and API overhead that a solo developer doesn't need.
- Existing agent frameworks are heavy, and their state is hidden in opaque systems.
- Letting an LLM freely edit a project board or push code is unreliable and unsafe without a deterministic control layer and guardrails.

## 3. Goals

1. Turn a well-specified task into a green, **closed** (merged) PR with no manual steps between "task written" and "PR closed", unless an escalation is triggered, in which case human approval is required (section 8.4a).
2. Keep all state (tasks, logs, rules) in plain text inside the repo, so it is diffable and versioned.
3. Keep the human in control only when escalation is required: tasks that trip an escalation trigger (section 8.4a) never merge without explicit human approval. Nothing is deployed in v1. Tasks that pass every gate with no trigger complete on their own.
4. Stay small enough to understand and modify in an afternoon (single Python package, few dependencies).
5. Stay model-agnostic behind one agent-runner interface; v1 runs on Claude Code (Pro subscription). Local models are not a goal.
6. Work on both **existing** projects (`adlc init`: adopt the repo's conventions) and **new** projects (`adlc new`: scaffold a minimal, ready-to-use repo), using the same board format and the same orchestrator. Anything specific to a repo or stack lives in a profile (config), not in code.
7. Build quality in at every stage, not only at review: each lifecycle stage uses a named, pinned method (section 7.3), such as a grilling interview to sharpen a task, test-driven development to implement it, a diagnosis routine when stuck, and a two-axis code review before merge.

## 4. Non-Goals

- Multi-user collaboration, permissions, or a web UI.
- Replacing Jira feature-for-feature (sprints, estimates, reporting).
- Deployment of any kind in v1; autonomous deployment to production is a permanent non-goal (Gate 5 stays human-approved).
- Local-model support.
- Handling large monorepos or multi-repo changes in v1.
- Autonomous task decomposition or planning from vague ideas. The unattended loop only runs tasks that are `READY` (with acceptance criteria). Turning an idea into a ready task is an interactive, human-present step (`adlc refine`, a grilling interview; FR-30).

## 5. User and Use Cases

**User:** one developer working on their own repositories, comfortable with git, Python, and the command line.

| # | Use case |
|---|----------|
| UC1 | Add a task with description and acceptance criteria to the board, run `adlc run`, and get a PR. |
| UC2 | Inspect what an agent did and why, using the run log for a task. |
| UC3 | Re-run or abandon a task that failed or got stuck. |
| UC4 | Handle an escalation: review the escalated task, then approve and merge, send it back with feedback, or reject it. |
| UC5 | Leave a batch of well-specified tasks running; come back to closed PRs and a short list of escalations. |

## 6. Scope

### In scope (v1 / MVP)
- Task board on `docs/WORK-ITEMS.md` with per-item briefs, and a state machine
- Deterministic orchestrator CLI
- Tester, Coder and Reviewer agent roles with a bounded fix loop (Ralph loop, section 8.2a)
- Escalation policy and `NEEDS_HUMAN` queue (section 8.4a)
- Git branch/worktree handling, PR creation, and conditional auto-merge (GitHub), driven by a per-repo profile (BOSS NextGen is the reference profile, section 13.1)
- `adlc init` (existing repos) and `adlc new` (fresh repos), with `dotnet` and `python` profiles (FR-29, section 13.1)
- The methods layer: pinned skills used at each lifecycle stage (section 7.3, FR-30 to FR-37)
- Per-task run logs and iteration, time, and usage-window limits
- `AGENTS.md` guardrails

### Out of scope for v1 (future)
- Any deployment, GitLab support, multiple concurrent tasks (M5), an unattended task-planning agent, local models, a visual dashboard, board formats other than the `WORK-ITEMS.md` table (adapters are a documented extension point only), and stacks beyond the `dotnet` and `python` profiles.

## 7. Architecture Overview

Layout of the v1 target repository (BOSS NextGen). Items marked *existing* are already in the repo; ADLC adds only `.adlc/` and `docs/work-items/`.

```
target-repo/
├── CLAUDE.md               # existing: short, always-loaded index (working agreements, architecture one-liners, standing lessons)
├── .adlc/                  # new: ADLC's own files; agents may not edit it
│   ├── AGENTS.md           # per-agent roles, permissions, and boundaries (enforced by the orchestrator)
│   ├── config.yaml         # models, limits, DoD commands, protected areas, context codes, git/PR settings
│   └── logs/               # one folder per task run: prompts, diffs, test output, exit codes, usage (the execution log)
├── adr/                    # existing: one numbered ADR per decision (numbers reserved up front)
├── docs/
│   ├── WORK-ITEMS.md       # existing: the task board (table). ADLC edits only the Status and Notes cells
│   ├── work-items/         # new: one brief per item, {KEY}.md (description, acceptance criteria, front-matter)
│   └── quality-gates/      # existing: Gate 1 to 5 checklists with sign-off block
├── src/, tests/, build/    # existing: application code, tests, and the build/*.sh scripts used as DoD commands
└── .github/
    ├── pull_request_template.md   # existing: requirement linkage, tests, risks, gate checklist
    └── workflows/                 # existing: CI (runs on PRs)
```

**Design principle:** the orchestrator (`adlc`) is deterministic code that owns the board, git, and the state machine. LLM agents only do work that needs judgment: writing code, writing tests, and describing the change. Agents never edit the task board (`docs/WORK-ITEMS.md`) directly; they return results to the orchestrator, which applies structured updates.

### Components

| Component | Type | Responsibility |
|-----------|------|----------------|
| Orchestrator | Plain code | Selects the next task, enforces state transitions, manages worktrees, retries, limits, and logging. |
| Ralph loop controller | Plain code | Part of the orchestrator. Repeats a fresh-context agent iteration on the same task prompt until the orchestrator's own completion check passes or a stop condition fires (8.2a). |
| Coder agent | LLM | Implements the task in the worktree, within the allowed paths. |
| Tester agent | LLM | Writes tests derived from the acceptance criteria before the code exists. It never edits production code. |
| Reviewer agent | LLM | Independent of the Coder (separate prompt and a different model). Reviews the diff against the acceptance criteria, `AGENTS.md`, the DoD and the quality gate, and returns approve / changes-requested / escalate with reasons. It never edits code. |
| Escalation engine | Plain code | Evaluates the escalation triggers (8.4a) after every iteration and before merge. |
| PR step | Code + LLM | Pushes the branch, opens the PR, drafts the description with evidence attached, and merges only if no escalation is open. |

### 7.1 Process borrowed from BOSS NextGen

| Practice in BOSS NextGen | How ADLC adopts it |
|---|---|
| `docs/WORK-ITEMS.md`: one row per work item, added **before** the branch is created, so each branch/PR has a traceable entry | The task board is that log. A task must exist (with context code and requirement link) before a run can start. |
| Context codes (`SHIP`, `DECL`, `PLAT`...) in keys and branch names: `feature/{CODE}-{seq}-{slug}` | Task IDs are `{CODE}-{seq}`. Branches follow the same prefix set (`feature/ bugfix/ hotfix/ refactor/ spike/ docs/ test/`). Codes are defined in `config.yaml`. |
| Conventional Commits | The Coder's commits must match `type(scope): subject`; the orchestrator rejects others. |
| PR template with linked requirement, API/DB changes, testing, risks, reviewer checklist | The PR step fills the same template from the task and the run log. |
| Definition of Done (traceable requirement, tests with enforced coverage thresholds, lint, dependency scan, secret scan, docs, ADR, audit logging) | The DoD is a configurable checklist in `config.yaml`; the orchestrator runs every automatable item and a task cannot reach `IN_REVIEW` until they pass. |
| Quality gates 1 to 5 as living checklists with a sign-off block | Already in the repo at `docs/quality-gates/`; ADLC reuses them as they are. The relevant gate is attached to the PR and completed by the Reviewer agent with evidence; a human signs off only escalated tasks. |
| ADR for every architectural decision, with the number reserved up front when work runs in parallel | A task may require an ADR. The orchestrator assigns the ADR number in the task brief. |
| Execution log per day/task with commands, exit codes, test and coverage output, evidence files | The per-run log folder is this record (FR-18). |
| "Done" means proven against the running system, with **unpiped exit codes** | Gates run real commands and the orchestrator captures the real exit code itself. It never accepts an agent's statement that tests passed. |
| "Agent self-reported durations were 2-4x the real ones: verify from outputs, not narrative" | Metrics (time, tokens, cost) are measured by the orchestrator from API usage and timestamps, never taken from agent output. |
| "Prove a test can fail" (fault injection) | The Tester must show the new tests fail against the unmodified code before the Coder implements (red) and pass after (green). |
| `CLAUDE.md` kept small, with detail pushed to ADRs and logs; a "Standing Lessons" list of recurring mistakes | `AGENTS.md`/`CLAUDE.md` holds working agreements and standing lessons. Failed runs can record a proposed lesson in the run log; only the human adds it to `CLAUDE.md`. |
| Commit and push only when explicitly requested; `main` protected; PR-to-self even when solo; squash or rebase merge | Every change still goes through a branch and PR, and `main` stays protected. The difference: the user grants standing authority once, in `config.yaml`, for the runner to commit, push, open a PR, and merge non-escalated tasks. Escalated tasks fall back to explicit approval. The runner never bypasses branch protection. |
| Work proceeds one task at a time unless parallelism is explicitly planned | v1 runs tasks strictly one at a time, in sequence (parallelism is M5, see 7.2). |
| `handoff` skill: summarise a session for a fresh agent, referencing artifacts instead of copying them | `adlc handoff TASK-ID` writes a short resume note that links the log, branch, and failing output. |

### 7.2 Parallel work (v2, not v1)

The BOSS NextGen pilot built three tasks in parallel with worktree agents. Measured result: about 1.5 to 2 times faster in active time, **zero** merge conflicts, no gain in calendar time because the human was the bottleneck, and about 27 minutes of integration work. Its recommendations shape this PRD:

- Parallelism is a later milestone (M5), used only for independent work on disjoint files. It is not used for strict build chains.
- Prerequisites: an **ownership table** of shared files (solution file, project references, Dockerfiles, compose file, CI config, `CLAUDE.md`, ADR numbers). Shared files are edited by one owner, the orchestrator, at integration. Branches may only propose changes.
- Each parallel worker gets its own worktree **and** its own runtime slot (ports, compose project name, volumes). A worktree isolates code, not Docker.
- The ceiling is the machine's CPU and memory, not the tooling. Default `max_parallel` is 2.
- The integrator re-runs all checks on the merged tree. Workers hand back files and numbers (commands, exit codes, coverage, evidence files), not summaries.
- Cross-cutting documentation is reconciled by the orchestrator after merge, since that is where semantic overlaps that git cannot see (stale statements, duplicated work) appeared.
- Environment friction to handle up front: line endings on Windows worktrees (`.gitattributes`), and sandboxed shells that block `eval`.

### 7.3 Lifecycle and methods (skills)

Each stage of the lifecycle has a named **method**: a pinned skill (from the `mattpocock-skills` set, plus project-local skills) that the orchestrator tells the agent to follow. The orchestrator still owns the state machine, the exit codes and the merge; the method shapes how the agent does its judgment work.

| Stage | Who is present | Method (skill) | What it produces |
|---|---|---|---|
| **Frame** an idea or an under-specified task | Human + agent (interactive) | `grilling`: one design-tree interview, a round of numbered questions each with a recommended answer, until nothing is silently assumed | A `READY` task: row plus brief with acceptance criteria, dependencies, ADR need |
| **Name things** (new concepts, terms, decisions) | Human + agent | `domain-modeling`: glossary and ADR discipline | `GLOSSARY.md` entries and ADR drafts; the agents then use the project's own terms |
| **Learn before building** (unknown API, library, standard) | Agent, unattended | `research`: primary sources only, findings saved as a Markdown file | `docs/research/<topic>.md` linked from the brief; no code |
| **Try before committing** (design or UI question) | Agent, unattended | `prototype`: throwaway, on a `spike/` branch, never merged | A recorded answer to the design question; the branch is deleted |
| **Design** a module | Agent | `codebase-design`: deep modules, seams, testability vocabulary | Interface and boundary choices recorded in the brief or ADR |
| **Implement** | Agent, unattended (Ralph loop) | `tdd`: red, green, refactor; tests first, one behaviour at a time | Failing tests, then the code, per iteration (FR-8, FR-9c) |
| **Stuck or bug fix** | Agent, unattended | `diagnosing-bugs`: reproduce, minimise, hypothesise, instrument, fix, regression-test | One diagnosis iteration before a stuck loop escalates; the method for `bugfix/` tasks |
| **Review** | Reviewer agent (different model) | `code-review`: two parallel axes, **Standards** (the repo's documented rules) and **Spec** (the originating task and its acceptance criteria) | Approve / changes-requested / escalate with findings on both axes |
| **Deliver** | Agent | `pr`: PR body conventions | The PR description, filled into the repo's template |
| **Human-only step** (create the repo, protect `main`, add secrets, external dashboards) | Human, guided | `wizard`: generates an interactive script that walks the person through each step | A script in `.adlc/wizards/`; used by `adlc new`, `adlc init`, and as the "suggested next step" of an escalation |
| **Author agent context** (`AGENTS.md`, `CLAUDE.md`, project skills) | Agent + human | `writing-for-agents`: short, structured, always-loaded context | The index files in the target repo, kept small (FR-37) |
| **Resume** | Agent | `handoff` (project-local skill, from BOSS NextGen) | A resume note linking artifacts instead of copying them (FR-18b) |

Rules for using methods:
- **Interactive methods never run unattended.** `grilling`, `domain-modeling` and `wizard` need the person. They run in `adlc refine`, `adlc init`, `adlc new`, and when an escalation of type "spec problem" is retried (FR-30). Unattended stages use only the non-interactive methods.
- **Methods are third-party instructions, so they are pinned and reviewed** (FR-33). The pinned version is recorded in `config.yaml`; changing it is a protected-area change.
- **A method never replaces a check.** `tdd` does not replace the orchestrator running the tests; `code-review` does not replace CI. Completion is still decided by orchestrator-run commands (FR-9d, FR-10a).
- **Model-agnostic fallback:** each method has an equivalent prompt in `.adlc/methods/`, used when the runner is not Claude Code or the skill is unavailable (FR-34).

## 8. Functional Requirements

### 8.1 Task board
- **FR-1** In v1 the task board is BOSS NextGen's existing `docs/WORK-ITEMS.md`, a table with the columns Key, Context, Title, Status, Linked PRD Chapter / Day, and Notes. The Key is `{CONTEXT-CODE}-{seq}` (for example `PLAT-003`). ADLC does not change the column layout. The existing statuses (`Open` and so on) map to ADLC states: `Open` = `BACKLOG`.
- **FR-2** ADLC needs more than the table holds (description, acceptance criteria, priority, dependencies, ADR number, `requires_approval`). Each item therefore has a **task brief** file, `docs/work-items/{KEY}.md`, linked from the Notes cell, with YAML front-matter for those fields. A validator rejects an item without a brief, without acceptance criteria, or with a malformed brief, and the item is not picked up. The orchestrator edits only the Status and Notes cells (state, reason, PR URL, log link) and never rewrites other rows. *This table-plus-brief split is a design choice made to honour the "keep `WORK-ITEMS.md`" decision; revisit it if it proves clumsy.*
- **FR-3** The Status cell holds the state, visibly. States: `DRAFT` (an idea, not yet sharpened) `→ BACKLOG` (ready: the brief is complete and validated, normally produced by `adlc refine`; BOSS's `Open` maps here; only `BACKLOG` items are picked up, so "READY" elsewhere in this document means `BACKLOG`) `→ IN_PROGRESS → IN_REVIEW → DONE` (merged and closed), plus `NEEDS_HUMAN` (escalated, from any active state), `BLOCKED` (unmet dependency), and `FAILED` (rejected or abandoned). A task waiting for the subscription usage window to reset (FR-9i) keeps its state and shows `PAUSED` in Notes. Illegal transitions are rejected.
- **FR-4** Only the orchestrator writes the board. Writes are atomic (temp file + rename) and guarded by a lock file.
- **FR-4a** Where the board lives: the live board is the local `docs/WORK-ITEMS.md` in the primary checkout, which only the orchestrator edits (FR-4). Because `main` is protected, status changes are not pushed to `main` on their own. The task's own PR carries the final row update (Status `DONE`, PR link), authored by the orchestrator and exempt from the protected-area trigger. Intermediate and failure states (`IN_PROGRESS`, `NEEDS_HUMAN`, `FAILED`) therefore exist in the local file until the next merged PR commits it. Accepted for v1 (decision 9).
- **FR-5** Task IDs are allocated by the CLI (`adlc add`, which adds the row and a brief skeleton) and never reused.

### 8.2 Run loop
- **FR-6** `adlc run [TASK-ID]` picks the named task, or the highest-priority `BACKLOG` task whose dependencies are `DONE`, and moves it to `IN_PROGRESS`.
- **FR-7** Each task runs in its own git worktree on branch `<type>/<task-id>-<slug>` for feature, bugfix and hotfix (for example `feature/SHIP-001-shipment-creation`) or `<type>/<slug>` for refactor, spike, docs and test, matching the BOSS NextGen convention; it is created from an up-to-date base branch. The primary checkout's code is never modified; only the board file and `.adlc/logs/` are written there.
- **FR-8** The Tester writes tests from the acceptance criteria before the Coder implements (test-first). The orchestrator confirms the new tests **fail** on the unmodified code (proof they can fail), then the Coder implements until they pass.
- **FR-9** The fix loop is a Ralph loop with hard bounds, defined in 8.2a. Exceeding any bound escalates the task (8.4a) instead of silently failing.
- **FR-10** The Definition of Done is a checklist in `config.yaml`. Each automatable item is a command the orchestrator runs itself: build, lint/format, unit and integration tests, coverage thresholds (e.g. 95% core logic, 80% CRUD or shared libraries), dependency vulnerability scan, secret scan, and static analysis. Failures are fed back into the loop. Non-automatable items (docs updated, ADR added, OpenAPI/AsyncAPI updated, audit logging verified) are checked by the Reviewer agent and recorded in the PR.
- **FR-10a** **Evidence rule:** a task is `DONE` only when each DoD command has been run by the orchestrator with its exit code captured directly (not through a pipe), and the output is saved in the run log. An agent's claim that something passed is never accepted as evidence. Where the project has a runnable stack, an end-to-end smoke test against it is part of the DoD.
- **FR-10b** Commits follow Conventional Commits (`feat(scope): ...`). The orchestrator rejects non-conforming messages.
- **FR-11** On success the orchestrator commits, pushes, opens a PR using the repo's PR template (linked requirement, impacted modules, API/DB changes, tests, risks, applicable gate checklist), attaches the test log, and moves the task to `IN_REVIEW`. The Reviewer agent completes the gate checklist in the PR with evidence. A human signs it off only for escalated tasks.
- **FR-11a** If the task declares an ADR, the orchestrator reserves the next ADR number before the Coder starts and the Coder drafts the ADR from the repo template. ADR numbers are never chosen by agents.
- **FR-12** Auto-merge is the default for non-escalated tasks: when the Reviewer approves, local DoD evidence is complete, remote CI and required checks are green, and no escalation trigger is open, the orchestrator merges (squash or rebase, per the repo's rule), closes the PR, deletes the branch and worktree, and sets the task to `DONE`. Merge goes through the repo's normal branch protection; the orchestrator never bypasses it. An escalated task waits in `NEEDS_HUMAN` and merges only after an explicit `adlc approve TASK-ID`. Setting `auto_merge: false` in `config.yaml` restores approval for every task.
- **FR-12a** Deployment: v1 deploys nothing, to any environment. A merged PR ends the task. Promotion between environments (Gate 5) stays a separate, human-approved activity outside ADLC.
- **FR-13** Interrupted runs can be resumed (`adlc resume TASK-ID`) or reset (`adlc reset TASK-ID`). A task stuck in `IN_PROGRESS` beyond a timeout is flagged by `adlc status`.

### 8.2a Ralph loop

The "Ralph loop" technique runs the same prompt against an agent repeatedly, each time with a fresh context, so progress lives in files and git rather than in the model's memory. ADLC adopts it for the implement-and-fix phase and moves the stop decision out of the agent's hands.

- **FR-9a** Each iteration is a new agent invocation with the same inputs: the task brief (description, acceptance criteria, linked ADR), `AGENTS.md`, the repo map, and the carried state described below. No conversation history is carried between iterations.
- **FR-9b** Carried state is files in the worktree and the run log: the current diff and git history of the branch, `progress.md` (what was tried, what failed, what remains, written by the agent each iteration), and the last verification output (failing test names, stack traces, lint messages), trimmed to a size limit.
- **FR-9c** Each iteration has one job: pick the next failing item, fix it, and run the quick checks. It then ends. This keeps context small and diffs reviewable.
- **FR-9d** **Completion is decided by the orchestrator, not the agent.** The agent may print a completion promise (for example `<promise>COMPLETE</promise>`), but it only triggers the check. The loop exits as successful only when every DoD command exits 0 and the Reviewer agent approves (FR-10a). A false completion claim is logged and the loop continues.
- **FR-9e** Stop conditions (any of them ends the loop):
  - success (FR-9d);
  - `max_iterations` reached (default 5 for the Coder loop);
  - `max_task_minutes` reached (or `max_cost_usd`, if set);
  - **stuck detection:** the same failure signature (for example the same failing test with the same error) appears in 2 consecutive iterations, or the diff stops changing;
  - **regression detection:** an iteration makes more previously passing checks fail than it fixes, in which case the orchestrator reverts the iteration's changes and counts it;
  - a guardrail violation (edit outside allowed paths, forbidden command).
  Every stop other than success raises an escalation (8.4a), except a usage-window pause (FR-9i).
- **FR-9f** Loop shape: Tester writes failing tests (once) → Ralph loop of Coder iterations until the DoD passes → Reviewer pass. If the Reviewer requests changes, its findings are added to the carried state and the loop resumes. At most `max_review_rounds` (default 2) rounds are allowed before escalation.
- **FR-9g** Tests are protected during the loop: the Coder may not modify or delete tests written by the Tester. The Coder can propose a test change in `progress.md`, and any such proposal escalates (to avoid a loop that passes by weakening the tests).
- **FR-9h** `adlc run --max-iterations N` and an `adlc loop-status` view show iteration count, token usage so far, last failure signature, and stop reason.
- **FR-9i** Subscription limits: when the provider returns a rate-limit or usage-window error, the orchestrator saves state, marks the task `PAUSED` in Notes (not `FAILED`, no escalation, no iteration counted), and resumes automatically when the window resets. `max_tasks_per_window` caps how many tasks are started per usage window. Models are set per role: a mid-tier model for the Coder and Tester, a different, stronger model for the Reviewer only, and the cheapest model for PR descriptions and `progress.md` summaries.

### 8.3 Context handling
- **FR-14** The Coder receives the task, `AGENTS.md`, a repository map (file tree plus symbol summary), and the contents of files it asks for or that search selects. It must not rely on loading the entire repo.

### 8.4 Guardrails (`AGENTS.md`)
- **FR-15** `AGENTS.md` defines per-agent: allowed paths, forbidden paths (e.g. `.github/`, `.env`, `.adlc/`), allowed shell commands, and coding conventions. The orchestrator enforces path and command rules and does not rely on the model to obey them.
- **FR-16** Secrets are never placed in prompts or logs. The git/PR token is scoped to the single repository.
- **FR-17** Task text and repository content are treated as untrusted input. Agents run with no network access beyond the model API, and no credentials beyond what the PR step needs.

### 8.4a Escalation policy

A task runs to a closed PR without a human unless at least one trigger fires. Triggers are evaluated by the orchestrator (not the agents) after every iteration and again immediately before merge.

| Trigger | Examples |
|---------|----------|
| Loop exhausted | `max_iterations`, `max_task_minutes`, stuck or regressing loop (FR-9e) |
| Review unresolved | Reviewer returns `escalate`, or still requests changes after `max_review_rounds` |
| Protected area touched | The per-repo list in `config.yaml` (BOSS NextGen list in 13.1): CI/CD config, secrets or environment files, auth and security code, database migrations, dependency manifests with new packages, shared files, anything under `.adlc/`. The orchestrator's own board row and `CLAUDE.md` index line (FR-4a, FR-18a) are exempt; agents may never edit those files. |
| Scope and size | Diff larger than `max_diff_lines`; files changed outside what the task implies; new top-level dependencies |
| Quality regression | Coverage drops below the threshold, a new High/Critical advisory, secret scan finding, lint or static analysis gate newly failing |
| Test integrity | Coder proposes changing or removing a Tester-written test; tests cannot be shown to fail first |
| Spec problem | Acceptance criteria are ambiguous or contradictory (the Tester or Reviewer flags it), or the task needs a new ADR the task did not declare |
| External check | Remote CI fails after local DoD passed, or merge is blocked by branch protection |
| Guardrail | Any attempted action outside `AGENTS.md` permissions |
| Task flag | The task is tagged `requires_approval: true` |

- **FR-23** On a trigger the orchestrator stops work, sets the task to `NEEDS_HUMAN` with the trigger, evidence links, and a suggested next step, and, only if a PR already exists (for example remote CI failed after the PR was opened), labels it `needs-human` and converts it to a draft. Later tasks still run, one at a time (v1 is sequential).
- **FR-24** The human responds with one of: `adlc approve TASK-ID` (merge as is), `adlc retry TASK-ID --note "..."` (feedback added to the carried state, loop budget reset), `adlc edit TASK-ID` (change the task, then retry), or `adlc reject TASK-ID` (close PR, mark `FAILED`).
- **FR-25** Notification of escalations is local to start with (terminal summary and `adlc status`), with a hook for desktop notification or webhook later.
- **FR-26** Escalation rates are reported (`adlc report`): by trigger, by task type, and by how often the human overrode the agents. Triggers that always get approved unchanged are candidates to relax; triggers that catch real problems stay.
- **FR-27** **Failed tasks are visible on the board, with no draft PR.** When a task ends in `FAILED` (rejected, abandoned, or unrecoverable) or stops in `NEEDS_HUMAN` because the loop gave up, the orchestrator opens no draft PR for the partial work. It sets the Status cell to `FAILED` or `NEEDS_HUMAN` and writes the reason, the trigger and a link to the run log into the Notes cell. `adlc status` lists these first. The branch and worktree are kept locally until the task is retried or reset.

### 8.5 Observability
- **FR-18** Each run writes `logs/<task-id>/<run-n>/`, which serves as the task's execution log: prompts and responses, plan, diffs per iteration, every verification command with its exit code and output, bugs found and fixed, files touched, token usage, and estimated cost. Time and token figures are measured by the orchestrator (timestamps, API usage fields), never copied from agent self-reports.
- **FR-18a** After a run, the orchestrator adds a one-line status for the task to the index in `CLAUDE.md`/`AGENTS.md` (detail stays in the log and ADR). This line is authored by the orchestrator and travels in the task's PR. Failed runs may record a proposed "standing lesson" in the run log, which only the human adds to `CLAUDE.md`.
- **FR-18b** `adlc handoff TASK-ID` writes a short resume note (state, branch, last failure, suggested next step) that links to artifacts instead of duplicating them, with secrets redacted.
- **FR-19** `adlc status` shows the board, current states, and the last result per task. `adlc log TASK-ID` prints the latest run summary.
- **FR-20** Config supports optional per-task limits: `max_task_minutes` and, only when an API key is used, `max_cost_usd` (off by default on the Claude Code subscription). Reaching a limit stops the run and escalates (8.4a).
- **FR-21** `--dry-run` shows the selected task, planned branch, and prompts without calling a model or touching git.

### 8.6 Model configuration
- **FR-22** The implementation is model-agnostic behind one agent-runner interface. v1 drives agents through Claude Code on the user's Pro subscription. `config.yaml` selects the model per role (Coder, Tester, Reviewer, PR writer), and the Reviewer's model must differ from the Coder's. Local models are out of scope for v1.

### 8.7 Methods layer (skills)
- **FR-30** `adlc refine [KEY|"idea"]` starts an interactive `grilling` session. It reads the repo context (`CLAUDE.md`, ADRs, glossary, the board) first and looks up facts itself instead of asking the person for them. It asks in rounds of numbered questions, each with a recommended answer, until nothing is silently assumed, and the person confirms the shared understanding. It then writes the row and the brief (acceptance criteria, dependencies, `requires_approval`, ADR need) and the validator (FR-2) must pass before the item becomes `BACKLOG`. A "spec problem" escalation (8.4a) can be retried with `adlc retry TASK-ID --refine`, which reopens the grilling session on that brief.
- **FR-31** Stage-to-method mapping is data (`config.yaml: methods:`), defaulting to the table in 7.3. The orchestrator injects the method for the current stage into the agent's instructions and records which method and version were used in the run log.
- **FR-32** Unattended stages use these methods automatically: `tdd` for the Tester and Coder (one failing behaviour per Ralph iteration), `diagnosing-bugs` for `bugfix/` tasks and for the single diagnosis iteration run when stuck detection fires (8.2a, FR-9e: a stuck loop gets one diagnosis iteration before escalating, and the diagnosis report is attached to the escalation), `code-review` for the Reviewer (Standards axis against `AGENTS.md`, the DoD and the repo's documented rules; Spec axis against the brief and acceptance criteria; findings reported per axis), and `pr` for the PR body.
- **FR-33** Supply chain: the skill set is installed at a pinned version, the version and a content hash are stored in `config.yaml`, `adlc doctor` verifies them, and an update is a protected-area change reviewed by the human. Skill text is treated as instructions the user chose to trust, but it cannot widen permissions: `AGENTS.md` path and command rules are enforced by the orchestrator regardless of what a skill says.
- **FR-34** Fallback: `.adlc/methods/<name>.md` holds a short equivalent prompt for each method so the loop still works when the runner is not Claude Code or a skill is missing. `adlc doctor` reports which methods are available as skills and which use the fallback.
- **FR-35** `research` and `prototype` tasks: a brief of type `spike` may request them. `research` writes `docs/research/<topic>.md` with sources and no code. `prototype` works on a `spike/` branch, records the answer, is never merged, and the branch is deleted. Both end in `DONE` with the written answer, and a task that depends on the spike unblocks.
- **FR-36** `wizard`: any step only a person can do (create the remote, protect `main`, add secrets or tokens, configure an external dashboard) is emitted as an interactive script in `.adlc/wizards/<name>.sh` instead of prose. `adlc init`, `adlc new` and escalations reference it. Scripts run only when the person runs them.
- **FR-37** `writing-for-agents` governs the files agents always load (`CLAUDE.md`/`AGENTS.md`, project skills): keep them short, structured, and pointing to ADRs and logs rather than copying them. `adlc doctor` warns when an index file exceeds `max_index_lines` (default 150).
- **FR-38** `domain-modeling`: `adlc init` and `adlc new` create `GLOSSARY.md` when missing; `adlc refine` proposes glossary entries and ADRs for new concepts or decisions it surfaces, and the human accepts or rejects each.

### 8.8 Onboarding: existing and new projects
- **FR-29** `adlc init` (existing repo) and `adlc new <name>` (fresh repo), both dry-run by default.
  - `init` inspects the repo: stack (from build files), test and lint scripts, CI workflows, default and protected branches, existing board or work-item log, PR and ADR templates, `CONTRIBUTING.md`, `CLAUDE.md`. It proposes a `config.yaml` by choosing a stack profile and overriding commands that differ, shows a plan of every file to be added or changed, and writes nothing until the person confirms. It adds only `.adlc/`, `docs/work-items/`, a `GLOSSARY.md` if missing, and a pointer in `CLAUDE.md`, and the result is delivered as a normal branch and PR, never a direct commit to the base branch.
  - `new` scaffolds from templates: `.gitattributes`, board (`docs/WORK-ITEMS.md`), `docs/work-items/`, `CLAUDE.md`/`AGENTS.md`, `GLOSSARY.md`, ADR and PR templates, gate checklists, a CI workflow for the chosen stack, a first ADR recording the stack choice, and the `.adlc/` files. Repo creation and branch protection are emitted as a `wizard` script (FR-36).
  - After either, `adlc doctor` runs the profile's DoD commands once against the unmodified repo and reports which pass. A task cannot run until the baseline is understood: pre-existing failures are recorded as the baseline, and only new failures count against a task.
- **FR-39** Profiles are config files, not code: `profiles/<stack>.yaml` holds DoD commands, coverage and lint commands, protected-area globs, branch and commit conventions, and test-first hints. v1 ships `dotnet` (derived from BOSS NextGen, section 13.1) and `python` (13.2). A repo's `config.yaml` extends a profile and may override any key.
- **FR-40** One board format everywhere: the `WORK-ITEMS.md` table plus briefs. A board adapter interface exists in the design for later formats, but v1 ships only this one.
- **FR-41** Safe defaults for projects with no history: `main` protected via the wizard, escalation on everything in the profile's protected list plus `.github/`, dependency manifests and secrets files, `auto_merge: false` until the baseline from `adlc doctor` is green and the person has approved the first three merges by hand (`adlc trust` flips the setting; it never flips itself).

## 9. Non-Functional Requirements

| Area | Requirement |
|------|-------------|
| Simplicity | Single Python package; core dependencies limited to the Claude Code runner, `pyyaml`, the `git` CLI (or `GitPython`), and the `gh` CLI (or PyGithub). ADLC's own tests use pytest. |
| Safety | No push to the protected base branch; no force-push; agent actions confined to the worktree. |
| Reliability | Board writes are atomic; a crash never leaves the board in a corrupt state. |
| Portability | Runs on Windows, macOS, and Linux. |
| Privacy | Code sent to the hosted model is limited to the files in the prompt. BOSS NextGen handles PAN and other personal data, so Highly Confidential fields and seed data must never be placed in prompts or logs (secret and PII scrubbing in M4). |
| Usage | Every run reports token usage and elapsed time. Limits are iterations, minutes per task, and tasks per usage window; no dollar cap on the subscription. |
| Testability | The orchestrator and state machine are unit-tested without calling an LLM, using a fake model client. |

## 10. Example Task

Illustrative only (`PLAT-008` is not a real item). A row in `docs/WORK-ITEMS.md`:

```markdown
| PLAT-008 | Platform (Diagnostics) | Add build version to the health response | Open | Phase 1 | Brief: docs/work-items/PLAT-008.md |
```

and its brief, `docs/work-items/PLAT-008.md`:

```markdown
---
key: PLAT-008
priority: 1
depends_on: []
adr: none
requires_approval: false
---
**Description:** The Api health endpoint should report the build version.

**Acceptance criteria:**
- The health response includes a `version` field
- The field is read from assembly metadata, not hard-coded
- An integration test covers the field being present
- No change to any other module
```

After a successful run the Status cell reads `DONE` and Notes holds the PR link and the run-log link; on a failure it reads `FAILED` or `NEEDS_HUMAN` with the reason (FR-27).

## 11. Success Metrics

| Metric | Target (MVP) |
|--------|--------------|
| End-to-end success | A well-specified small task reaches a merged, closed PR with no human step in ≥ 70% of attempts; the rest escalate rather than fail silently |
| Escalation quality | 100% of tasks that hit a trigger stop in `NEEDS_HUMAN` (none auto-merged); fewer than 20% of escalations are approved unchanged (otherwise relax that trigger) |
| Loop efficiency | Median iterations per task ≤ 3; share of loops ending by stuck detection tracked |
| Unsafe merges | 0 auto-merges with a failing check, a missing Reviewer approval, or an open trigger |
| Safety | 0 pushes to the base branch; 0 edits outside allowed paths |
| Usage | Median task finishes within `max_task_minutes`; fewer than 10% of tasks pause on the usage window |
| Recoverability | 100% of failed or interrupted runs leave a readable log and a board state the user can resume or reset |
| Evidence integrity | 100% of `DONE` tasks have orchestrator-captured exit codes for every DoD command; 0 tasks accepted on an agent's say-so |
| Traceability | Every branch, commit, and PR maps to a task ID and a linked requirement |
| Parallel gain (M5 only) | Measured against a sequential baseline from a clean start; adopt only if active-time gain is real after counting integration cost (the pilot saw about 1.5 to 2x) |
| Effort | Setup to first merged PR on BOSS NextGen in under 30 minutes |
| Onboarding, existing repo | `adlc init` on BOSS NextGen proposes a config whose DoD commands match `CONTRIBUTING.md` with no hand edits; baseline reported before any task runs |
| Onboarding, new repo | A repo made with `adlc new` passes its own CI on its first PR and takes a trivial task to a merged PR |
| Spec quality | Share of tasks that finish without a "spec problem" escalation is higher for tasks refined with `adlc refine` than for hand-written ones (tracked in `adlc report`) |
| Method fidelity | Run logs record method and version for every stage; 0 unpinned methods in use |

## 12. Milestones

| Milestone | Deliverable |
|-----------|-------------|
| M0: Foundations | `git init`, remote, branch protection (via a `wizard` script), pytest, CI, `CLAUDE.md`, pinned methods and `adlc doctor` |
| M1: Board | `WORK-ITEMS.md` table parser and writer (Status and Notes cells only), brief validator, state machine, `adlc add/status`, atomic writes, pytest unit tests, BOSS NextGen `.gitattributes` check |
| M2: Run loop | Worktree handling, Tester and Coder agents, Ralph loop with completion check, stop conditions, stuck/regression detection, logs, dry-run |
| M2a: Methods and profiles | Method registry and fallbacks (FR-31, FR-34), `tdd` and `diagnosing-bugs` wiring, `adlc refine` (grilling), profile loader with `dotnet` and `python` profiles, `adlc doctor` baseline |
| M2b: Review and escalation | Reviewer agent using two-axis `code-review`, escalation engine and triggers, `NEEDS_HUMAN` queue, `approve/retry/edit/reject` commands |
| M3: Delivery | Push, PR creation with test logs, `IN_REVIEW`, conditional auto-merge, branch/worktree cleanup, resume/reset, time and usage-window limits |
| M3b: Onboarding | `adlc init` (detect, propose, confirm, PR) and `adlc new` (template scaffold, wizard scripts), safe defaults and `adlc trust` (FR-29, FR-41) |
| M4: Hardening | Guardrail enforcement, secret scrubbing, prompt-injection review, CI workflow, quality-gate checklists, DoD config, ADR number reservation |
| M5: Parallel (optional) | Ownership table of shared files, per-worker runtime slots, `max_parallel`, orchestrator-owned integration and re-verification. Start only after a timed comparison of one task done sequentially vs. in parallel shows a real gain. |

## 13. Technology Choices

| Concern | Choice | Alternative |
|---------|--------|-------------|
| Task storage | `docs/WORK-ITEMS.md` plus `docs/work-items/{KEY}.md` briefs (v1) | YAML board, `tasks.json` (later) |
| LLM access | Claude Code on the Pro subscription behind an agent-runner interface | Direct API or LiteLLM later |
| Models | Mid-tier model for Coder and Tester; a different, stronger model for the Reviewer; cheapest for PR text | Local models: out of scope |
| Agent framework | None; plain Python orchestrator | CrewAI, AutoGen (not needed for a linear flow) |
| Git hosting | GitHub via `gh` | GitLab (future) |
| CI | BOSS NextGen's existing GitHub Actions (`CI passed`, `Analyze (csharp)`); ADLC's own repo needs a small pytest workflow | Local git hooks |

Visual workflow builders such as Dify are not part of the deployment gate. Merge gating is CI, Reviewer approval and the escalation policy, with human approval for escalated tasks.

### 13.1 `dotnet` profile and v1 acceptance target: BOSS NextGen

ADLC itself is written in Python and tested with pytest. The `dotnet` profile is derived from BOSS NextGen (`C:\Murale\Projects\CustomsCleranceNextGenApp`), the first and reference target, and the v1 acceptance test is one task taken to a merged PR there. The repo's `config.yaml` extends the profile (FR-39).

**Definition of Done commands** (the orchestrator runs each one itself and records the unpiped exit code; run from Git Bash on Windows):

| Check | Command (from `CONTRIBUTING.md`) |
|---|---|
| Build | `dotnet build` (no new warnings) |
| Lint / format | `bash build/scripts/lint.sh` |
| Unit tests and coverage thresholds (95% core logic, 80% CRUD or shared libraries; failing a threshold fails the build) | `bash build/scripts/test.sh` |
| Integration tests (infrastructure-free, no DB) | `bash build/scripts/integration-test.sh` |
| Dependency scan (no High or Critical advisories) | `bash build/scripts/dependency-scan.sh` |
| Secret scan (needs Docker) | `bash build/scripts/secret-scan.sh` |
| Container and end-to-end smoke | `bash build/scripts/smoke-test.sh`, `bash build/scripts/e2e-smoke-test.sh` |
| Contract tests (when an externally callable contract changes) | `bash build/scripts/contract-test.sh` |

**Auto-merge conditions (non-escalated tasks):** Reviewer approval, all DoD commands green, and on GitHub both required checks `CI passed` and `Analyze (csharp)` green. Merge is squash or rebase through the normal pull request, on protected `main`, with no admin bypass. The orchestrator never uses the documented emergency escape hatch.

**Protected areas (always escalate; proposed, to be adjusted):**
- `.github/workflows/ci.yml` and `build/azure-pipelines.yml` (must be edited together, per ADR-0005)
- `Dockerfile`s, `docker-compose.yml`, `appsettings*.json`, `.gitattributes`
- `CustomsClearance.sln`, `*.csproj` (new packages or project references)
- EF Core migrations
- Security and identity code, the gateway (`src/Gateway`), JWT and authorization policies
- Rules Engine (Decision Trace must be verified, per ADR-0002) and Audit (append-only, `audit_writer` role)
- `src/Api/Program.cs` (host-wide wiring only) and `TestApiFactory.cs`
- `CLAUDE.md`, `adr/`, `docs/quality-gates/`, and `docs/BRANCH-PROTECTION.md`

**Other BOSS NextGen rules the agents must follow** (loaded through `AGENTS.md`): a new API capability is a new `IApiModule` file, never an edit to `Program.cs`; the Standing Lessons in `CLAUDE.md` (for example, never trust a piped exit code, and prove a test can fail); and reference data is unverified placeholder data and must not be treated as authoritative.

### 13.2 `python` profile (proposed defaults, to be confirmed)

Needed for `adlc new` with a Python stack and for ADLC's own repository.

| Check | Command |
|---|---|
| Build / install | `python -m pip install -e .[dev]` |
| Lint / format | `ruff check .` and `ruff format --check .` |
| Types (if configured) | `mypy .` |
| Unit tests and coverage | `pytest --cov --cov-fail-under=<threshold>` (threshold set per repo; default 80) |
| Dependency scan | `pip-audit` |
| Secret scan | `gitleaks detect` |

Protected areas (default): `pyproject.toml` dependency changes, `.github/workflows/`, migration folders, anything under `.adlc/`.

## 14. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| LLM corrupts the task board | Agents never write the board; the orchestrator applies structured edits atomically |
| Tests that merely confirm buggy code | Tests derive from the acceptance criteria and are written first |
| Runaway loops (the main Ralph loop risk) | `max_iterations`, `max_task_minutes`, stuck and regression detection, escalation on any stop |
| Subscription usage window exhausted mid-task | Pause and resume (FR-9i), `max_tasks_per_window`, per-role model choice |
| Loop "passes" by weakening or deleting tests, or by faking completion | Tests are protected from the Coder; completion is decided by orchestrator-run commands plus an independent Reviewer, not by the agent |
| Autonomous merge of a bad change | Reviewer approval, remote CI, branch protection, escalation triggers on protected areas and size, `auto_merge: false` kill switch |
| Escalation fatigue or under-escalation | Escalation rates reported (FR-26); triggers tuned from data; start with conservative triggers |
| Unsafe code or commands | Worktree isolation, enforced path and command allowlists, protected base branch, scoped token |
| Prompt injection via task or repo text | Treat as untrusted, minimal permissions, no extra network or credentials, protected-area escalation, independent Reviewer agent. Because non-escalated PRs auto-merge, injection defence relies on the orchestrator's checks, not on human reading |
| Agent reports are inaccurate (BOSS pilot: self-reported durations were 2-4x real; stale or contradicting claims) | Verify from outputs; orchestrator measures time, tokens and exit codes itself |
| Parallel workers collide on shared files or hidden semantic overlaps | Ownership table, orchestrator-owned shared files, reserved ADR numbers, post-merge reconciliation and re-verification; parallelism deferred to M5 |
| Parallel runs exhaust the machine or collide on ports | Per-worker slots, `max_parallel` default 2 |
| Windows worktree line endings or blocked `eval` break checks | `.gitattributes` and non-`eval` fallbacks set up in M1 |
| Context too large for big repos | Repository map plus on-demand file selection |
| Third-party skills change behaviour or carry hostile instructions | Pinned version and content hash, `adlc doctor` check, update as a protected-area change, orchestrator-enforced permissions that skills cannot widen (FR-33) |
| Skills are Claude Code specific, against the model-agnostic goal | Equivalent fallback prompts in `.adlc/methods/` (FR-34); the orchestrator never depends on a skill for correctness |
| Interactive methods block unattended runs | They run only in `refine`, `init`, `new` and retries; the loop uses non-interactive methods only |
| `init` damages an existing repo's conventions | Dry-run default, full file plan shown, delivered as a PR, only additive files, baseline recorded before any task runs (FR-29) |
| Wrong profile or commands guessed for an unfamiliar stack | `init` proposes and the person confirms; `adlc doctor` runs the commands once on the unmodified repo |
| Fresh projects have no safety net (no CI history, thin tests) | Safe defaults: `auto_merge: false` until baseline is green and three merges are approved by hand (FR-41) |
| Board changes cannot be pushed to protected `main` on their own | Final row update travels in the task's PR; intermediate states live in the local file (FR-4a, decision 9) |
| BOSS NextGen DoD scripts need Docker, a long stack start, or hit the known RabbitMQ first-boot race | Orchestrator retries a failed start once (a plain second `up`, per the project's standing lesson) before counting a failure |

## 15. Decisions and Open Items

Items 1 to 8 and 10 to 15 are resolved; item 9 is a decision to revisit. Items 16 to 20 from the second grilling round are also resolved (listed at the end of this section).

1. Resolved: ADLC itself is a Python package tested with pytest. Its v1 **target repository** is BOSS NextGen (.NET 8, xUnit, coverlet), so the Definition of Done commands are the BOSS NextGen scripts (section 13.1). Python/pytest is the implementation language, not the language of the code the agents write.
2. Resolved: auto-merge of non-escalated tasks is in v1, customized to BOSS NextGen's branch protection (section 13.1). No deployment of any kind in v1; deployment stays out of scope (FR-12a).
2a. Resolved: yes, the Reviewer uses a different model from the Coder; the extra cost is acceptable.
2b. Resolved defaults for a Claude Code Pro subscription (tune after the first 10 to 20 tasks from `adlc report`): `max_iterations: 5`, `max_review_rounds: 2`, `max_diff_lines: 400` (production code, excluding tests, lockfiles and generated files; hard ceiling 800 including tests), `max_task_minutes: 30`, `max_cost_usd: null` on a subscription (set about 2.00 per task only when using an API key). On Pro the real budget is the usage window, so also set `max_tasks_per_window: 3` (see FR-9i).
2c. Resolved: the protected-area list is per repo in `config.yaml`. The BOSS NextGen list is in section 13.1 (proposed; the user to adjust).
3. Resolved (follow BOSS NextGen, `CONTRIBUTING.md` and ADR-0003): the base branch is `main`, protected, trunk-based, with no long-lived `develop` or `staging` branches; `release/*` branches only when preparing a production release. Branch names are `feature|bugfix|hotfix/{CONTEXT-CODE}-{seq}-{short-slug}` (for example `feature/SHIP-001-shipment-creation`) and `refactor|spike|docs|test/{short-slug}`. Merge is squash or rebase, keeping history linear. The base branch and context codes are configurable in `config.yaml` for other repos.
4. Resolved: the implementation is model-agnostic and runs on the user's Claude Code Pro subscription. Cost per task is not a v1 concern (no dollar cap; see 2b for the iteration, time and usage-window limits), and local models are out of scope.
5. Resolved: a failed task opens no draft PR; the run log is the record. The failure must be explicitly visible on the board (FR-27).
6. Resolved, **revised 2026-10-26**: ADLC is a generic tool with a generic core and per-stack profiles. BOSS NextGen (.NET 8, xUnit, coverlet, docker-compose, `build/scripts/*.sh`) is the first and reference profile and the v1 acceptance target. It also has to work on existing projects (`adlc init`) and fresh ones (`adlc new`).
7. Resolved: one board format for every project in v1: `docs/WORK-ITEMS.md` plus briefs. The YAML front-matter board is dropped (FR-1, FR-2, FR-40).
8. Resolved: see the gate table below. The per-PR versus per-phase mapping in its "Confirmed ADLC use" column was confirmed by the user on 2026-10-24.
10. Resolved (2026-10-26, confirmed "yes to all"): `PRD.md` is the canonical document; `PRD_ageinticSDLC.docx` is an older snapshot, to be regenerated from this file (not yet done).
11. Resolved (2026-10-26): generic core, BOSS NextGen as the first profile and v1 acceptance target (see 6).
12. Resolved (2026-10-26): two onboarding paths in v1, `adlc init` first and `adlc new` as a thin template scaffold (FR-29).
13. Resolved (2026-10-26): a single board format in v1; adapters are an extension point only (FR-40).
14. Resolved (2026-10-26): stack profiles are config; v1 ships `dotnet` and `python` (FR-39, 13.1, 13.2).
15. Resolved (2026-10-26): proven methods (skills) are part of the lifecycle at every stage (section 7.3, FR-30 to FR-38). Defaults in FR-41 (no auto-merge on fresh repos until trusted) and in 13.2 (Python commands) are proposals made by the assistant for the user to confirm.
9. **Decided for now (2026-10-24):** keep the FR-4a proposal for how board status reaches the protected `main`. The live board is the local file, and the task's PR carries the final row update. The catch is that `FAILED`, `NEEDS_HUMAN` and `IN_PROGRESS` exist only locally until a later PR commits them. Revisit if that proves a problem. Alternatives: a status PR per change (noisy, and each needs CI), a dedicated unprotected `adlc-board` branch, or keeping the live state in `.adlc/` (untracked) and treating `WORK-ITEMS.md` as a published summary only.

### Decisions from the 2026-10-26 grilling, round 2 (all confirmed)
16. Resolved: this repo stays the tool's home; `pip install -e` (or `pipx`) puts `adlc` on the path; no ADLC code is copied into target repos. Target repos only receive config and docs.
17. Resolved: `adlc doctor` checks the `mattpocock-skills` plugin version and content hash recorded in `config.yaml`; no vendored copy in v1.
18. Resolved: the default protected-area list for a repo with no profile match (FR-41) is `.github/`, dependency manifests, secrets and environment files, migrations, and `.adlc/`.
19. Resolved: `adlc refine` only proposes ADRs and glossary entries; the person accepts or rejects each (FR-38).
20. Resolved: three hand-approved merges before `adlc trust` (FR-41).
21. Resolved (2026-10-28): the implementation substrate is a **hybrid**. A thin deterministic driver (Python) keeps what must not depend on a model's compliance: the board (lock, atomic write, compare-and-set), the state machine, the limits and stop conditions, the usage-window pause, one fresh `claude -p` call per Ralph iteration, the DoD runner, git and the PR. Everything judgment-based or enforceable by Claude Code itself moves into Claude Code assets shipped to target repos: roles as subagents (`.claude/agents/`, with a `model:` per role so the Reviewer differs from the Coder), methods as skills, and guardrails as `PreToolUse` hooks plus `permissions.deny` rules for the board and protected areas. The driver still re-checks the diff after each iteration, so a hook that fails to fire cannot widen scope. A pure Claude Code build was rejected because the loop, limits and board would be enforced by the same model they exist to constrain. Work items re-scoped accordingly: ADLC-009, 010, 012, 015, 017, 019, 021 and 027 (notes in their briefs); new items ADLC-032 (substrate prototype) and ADLC-033 (Claude Code assets package).

No open items remain. The next step is to split this PRD into work items.

### Reference: quality gates available in BOSS NextGen (`docs/quality-gates/`)

| Gate | Name | When BOSS NextGen runs it | Main checks | Confirmed ADLC use |
|---|---|---|---|---|
| 1 | Architecture | Start of any new bounded-context module; Phase exit | Module in the right folder; Clean Architecture project split; no direct reference between bounded contexts; rules go through the Rules Engine; naming, branch and commit conventions; work item logged; sensitive-field handling; no committed secrets; no N+1 queries; ADR added or updated; event catalog updated | Per PR: the automatable subset (naming, conventions, secrets, cross-context references, ADR present). Full gate: human-signed at the start of a new module. |
| 2 | Backend | Completion of a context's domain, application and infrastructure layers, before its API | Business rule IDs implemented and traceable; entities with private setters; unit-test project with enforced coverage threshold (95% core logic, 80% CRUD or shared libraries); fakes, no real DB in unit tests; test naming; no new SonarAnalyzer warnings; no sensitive data logged; audit events for state changes; dependency scan clean | Per PR for any backend change (tests, coverage, static analysis, scan, audit are commands the orchestrator runs). |
| 3 | API | Completion of a context's API layer, before frontend use or external callers | OpenAPI spec matches the implementation; response envelope, ProblemDetails errors, correlation id; decimal money with currency; ISO 8601 UTC dates; URL versioning and breaking-change rule; policy-based authorization; integration tests (happy and failure paths); contract tests | Per PR whenever an endpoint or contract changes. |
| 4 | Frontend | Completion of a context's UI layer. **Not applicable until Phase 10**; no framework chosen yet | Stable versioned APIs only; ProblemDetails-based error messages; role-based UI; accessibility (WCAG 2.1 AA to be confirmed); performance targets; pagination; responsive design | Skipped as "Not Applicable" until a frontend exists. |
| 5 | Release | Each environment promotion (dev to staging to prod), not per context | Gates 1 to 3 passed for included contexts; end-to-end system integration; business acceptance by the Product Owner; full CI green with no disabled tests; load test against volume targets; backup and restore verified, rollback plan; SAST and dependency scan; DAST or penetration test for production; **manual approval for any production promotion, never automatic** | Never per PR and never automatic. Always human-approved, consistent with the production-deployment non-goal. |

All gates end with the same sign-off line: reviewed by, date, scope, and result (Pass, Pass with noted exceptions, Fail; Gate 4 also allows Not Applicable).
