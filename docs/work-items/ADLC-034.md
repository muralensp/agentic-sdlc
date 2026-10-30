---
key: ADLC-034
type: feature
milestone: M4
priority: 29
depends_on: [ADLC-019, ADLC-020, ADLC-021, ADLC-022, ADLC-027]
adr: none
requires_approval: true
---
# ADLC-034: `adlc run`: orchestrate one task end to end

**Description:** Join the finished parts into the command a person runs: pick a task, run the
Tester, the fix loop and the Reviewer, then hand the result to the PR step. Added because every
earlier item is a tested library function, but nothing calls them in order; the acceptance pilot
(ADLC-029) cannot run without it.

**Linked PRD:** FR-6, FR-8, FR-9, FR-11, FR-12, FR-13, FR-21, FR-23; 8.2a

**Acceptance criteria:**
- `adlc run [KEY]` takes the named task, or the best `BACKLOG` task whose dependencies are `DONE`, and
  moves it to `IN_PROGRESS`; it refuses when there is no baseline, when the usage window has no room, or
  when the task cannot be selected, and changes nothing in those cases
- The Tester writes tests in the task's worktree; changes outside the test paths, no tests, or tests that
  are not red on the unmodified code escalate (test integrity) instead of reaching the Coder
- The Tester's tests are committed and locked, then the Ralph loop runs with the Reviewer, the scrubber and
  the guarded runner; every call to a model goes through `GuardedRunner`
- A loop that stops on a usage limit pauses the task (it stays `IN_PROGRESS`); any other stop escalates the
  task to `NEEDS_HUMAN` with the reason and the run log on the board
- A loop that succeeds is handed to the PR step with the Reviewer's report, the facts of the diff (changed
  paths, production and test line counts, the task's approval flag) and the run log; the outcome (merged,
  waiting or escalated) is reported
- A method the registry refuses stops the run before the task moves, and nothing is created
- `--dry-run` still only prints the plan; `--max-iterations N` is honoured
- Tested end to end against the fake runner and fake forge in a real git repository, one scenario per
  outcome; the real `claude` and `gh` are never called by the tests

**Not in this item:** running on BOSS NextGen (ADLC-029), parallel tasks (ADLC-031), and the Claude Code
hooks and subagent files (ADLC-033).
