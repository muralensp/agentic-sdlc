# Glossary

Terms used across ADLC. Add an entry when a new concept appears; do not redefine one silently.

| Term | Meaning |
|---|---|
| **ADLC** | The tool: a local, file-based agentic SDLC that takes work items to merged pull requests. |
| **Target repo** | A repository ADLC operates on (an existing one adopted with `adlc init`, or a new one from `adlc new`). |
| **Board** | The task list in a target repo: the table in `docs/WORK-ITEMS.md`. |
| **Work item / task** | One row of the board, identified by a key such as `ADLC-001` (`{CODE}-{seq}`). |
| **Brief** | The per-item file `docs/work-items/{KEY}.md`: front-matter plus description and acceptance criteria. |
| **State** | Where an item is: `DRAFT`, `BACKLOG`, `IN_PROGRESS`, `IN_REVIEW`, `DONE`, `NEEDS_HUMAN`, `BLOCKED`, `FAILED`. |
| **Orchestrator** | The deterministic code that owns the board, git, the state machine, limits and logs. |
| **Agent / role** | An LLM invocation with a job: Tester, Coder, Reviewer, PR writer. |
| **Ralph loop** | Repeating a fresh-context Coder iteration on the same prompt, carrying progress in files and git, until the orchestrator's completion check passes or a stop condition fires. |
| **DoD** | Definition of Done: the profile's commands (build, lint, tests, coverage, scans) that the orchestrator runs itself. |
| **Baseline** | The results of the DoD commands on an unmodified repo, so only new failures count against a task. |
| **Profile** | A config file describing a stack: DoD commands, protected areas, conventions (`dotnet`, `python`). |
| **Method** | The named working approach used at a lifecycle stage, normally a pinned skill (grilling, tdd, code-review, ...). |
| **Escalation / trigger** | A condition that stops unattended work and puts the item in `NEEDS_HUMAN` for a person to decide. |
| **Protected area** | Files whose change always escalates (CI config, dependencies, migrations, security code, `.adlc/`). |
| **Gate** | One of BOSS NextGen's five quality-gate checklists (Architecture, Backend, API, Frontend, Release). |
| **Wizard** | A generated interactive script that walks a person through steps only a person can do. |
