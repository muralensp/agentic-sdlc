---
key: ADLC-039
type: bugfix
milestone: M4
priority: 39
depends_on: [ADLC-038]
adr: none
requires_approval: true
---
# ADLC-039: Pilot evidence: readable DoD records, a task-based PR title and body, `adlc finish`

**Description:** The PLAT-011 and PLAT-012 pilot runs (2026-11-02) reached a merged PR and a correct
`NEEDS_HUMAN`, and showed three gaps. Three of four `dod.json` files were invalid JSON, because the
loop scrubs every file in `verify-N/` as text and the card-number pattern eats part of a duration; those
files are the exit-code evidence (PRD section 11, evidence integrity). The PR was titled after the last
commit ("chore(loop): drop the progress notes"), which became the squash commit on `main`; its body was
wrapped in a Markdown fence and said no tests were run, because the writer never sees the measured DoD
results. And a task whose PR a person merged by hand cannot be closed from the CLI.

**Linked PRD:** FR-18, FR-21, FR-22; section 11

**Acceptance criteria:**
- Every JSON file the loop writes under `verify-N/` is valid JSON after scrubbing, with its exit codes
  intact; the `.out` and `.err` files are still scrubbed as text
- The PR title is `<type>(<key>): <task title>` with the type taken from the task type (`feature` to
  `feat`, `bugfix` and `hotfix` to `fix`, other commit types as they are, anything else `chore`), not
  from the last commit
- A writer reply wrapped whole in a `markdown` fence is unwrapped; code blocks inside it are kept
- The PR body carries a Verification section built from the last `dod.json`: each DoD command, its exit
  code and its time, measured by the orchestrator; the writer is told the tool adds it
- `adlc finish KEY` closes a task whose PR was merged on the host: it sets `DONE`, keeps the Notes text,
  removes the worktree, deletes the remote branch and the PR record, closes any open escalation as
  approved and counts the merge towards `adlc trust` (as the PR step does for an approved merge)
- `adlc finish` refuses, and changes nothing, when the PR is still open, was closed without a merge, or
  the task has no recorded PR

**Not in this item:** re-entering the merge step for an approved task whose PR is still open; the
scrubber's own patterns.
