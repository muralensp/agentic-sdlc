---
key: ADLC-038
type: bugfix
milestone: M4
priority: 38
depends_on: [ADLC-036, ADLC-037]
adr: none
requires_approval: true
---
# ADLC-038: Pilot blockers: the Reviewer ignores progress.md; run logs stay valid and readable

**Description:** The fifth and sixth PLAT-011 runs (2026-11-02) reached a finished implementation with
every DoD command green, then stopped as "stuck". The Reviewer flagged `progress.md`, a file the
orchestrator removes before the PR (ADLC-037), and the Coder could not remove it. The same runs showed
that `calls/*/result.json` can be invalid JSON, that the scrubber redacts the report's own labels, that
escalation reasons paste whole shell scripts, that `dotnet test ... | tail` is denied, and that
`adlc run --dry-run` crashes on a Windows console.

**Linked PRD:** FR-18, FR-19, FR-21

**Acceptance criteria:**
- The Reviewer's diff leaves out `progress.md` unless the base commit already has one
- Every `calls/*/result.json` is valid JSON even when a number looks like a card number; the scrubber is
  applied to the text values, not to the serialised JSON
- `summary.md` is not scrubbed a second time, so "Tokens:" and "secret_scan:" are kept; agent text in it
  (reason, findings, signature, denials, file names) is still scrubbed
- A denied tool call is shown as its first command, cut to a short length, at most three of them and a
  count of the rest, in escalation reasons and Notes; the full call stays in the run log
- The `dotnet` profile lets the agents run `tail` and `head`
- `adlc` output on a console that cannot show a character prints `?` instead of crashing

**Not in this item:** loosening the allow-list further (redirects, `rm`); the scrubber's own patterns.
