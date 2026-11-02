---
key: ADLC-037
type: bugfix
milestone: M4
priority: 37
depends_on: [ADLC-035]
adr: none
requires_approval: true
---
# ADLC-037: Profile-named agent commands; keep progress.md out of the pull request

**Description:** The third PLAT-011 run (2026-11-02) got through the Tester, Coder, DoD and Reviewer,
then showed two defects. The Coder could not run `dotnet test` or `dotnet format`: the ADLC-035 allow
list only holds the DoD commands, and for `dotnet` those are wrapper scripts, so the Coder worked
blind. And the Coder's `progress.md` was committed into the feature branch, so it would have landed in
the pull request; the Reviewer flagged it as out of scope.

**Linked PRD:** FR-15, FR-21; ADLC-035

**Acceptance criteria:**
- `guardrails.agent_commands` lists extra command prefixes the Tester and Coder may run; they are added to
  the allow rules after the DoD commands and plain git, and the deny rules still win
- The `dotnet` profile sets `dotnet build`, `dotnet test` and `dotnet format`; `generic` and `python` add
  none
- Before the PR step, the orchestrator removes `progress.md` from the branch in a commit of its own, so
  the pull request does not contain it; a `progress.md` that the base commit already had is left alone
- The notes stay in the branch history, and the run log is unchanged
- Tested with the fake runner in a real git repository; the `progress.md` test fails on the code before
  this item

**Not in this item:** the empty-reply and Notes-cell defects (ADLC-036); denials during the Coder's loop
(ADLC-036); a check in `adlc doctor` for a missing Reviewer model.
