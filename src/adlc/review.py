"""The Reviewer: a read-only agent on a different model judges the diff on two axes (FR-32, 7.1).

The Standards axis asks whether the code follows the repo's rules; the Spec axis asks whether it
does what the brief asked. The Reviewer also fills the gate checklists that apply to a PR: gates 1
to 3. Gate 4 is Not Applicable until a frontend exists and gate 5 is a human promotion step that
ADLC never runs. The driver supplies the diff, reads the verdict and keeps the limits
(`max_review_rounds` is enforced by the loop); a reply it cannot read escalates, not a guess.
"""

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from adlc.loop import DECISIONS, Review
from adlc.runner import AgentRequest, AgentRunner, Role

PASS, EXCEPTIONS, FAIL, NOT_APPLICABLE = (
    "Pass",
    "Pass with noted exceptions",
    "Fail",
    "Not Applicable",
)
RESULTS = (PASS, EXCEPTIONS, FAIL, NOT_APPLICABLE)
READ_ONLY = ("Read", "Grep", "Glob")
NO_EDITS = ("Edit", "Write", "NotebookEdit", "Bash")
_FENCE = re.compile(r"```(?:json)?[ \t]*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Gate:
    number: int
    name: str
    run: bool  # the Reviewer fills it in
    checks: str


GATES = (
    Gate(
        1,
        "Architecture",
        True,
        "module in the right folder; layer split and no cross-context references; rules in the "
        "rules engine; naming, branch and commit conventions; work item logged; sensitive-field "
        "handling; no committed secrets; no N+1 queries; ADR added or updated",
    ),
    Gate(
        2,
        "Backend",
        True,
        "business rules implemented and traceable; tests with the enforced coverage threshold; "
        "fakes, not a real database, in unit tests; no new analyzer warnings; no sensitive data "
        "logged; audit events for state changes; dependency scan clean",
    ),
    Gate(
        3,
        "API",
        True,
        "spec matches the implementation; response envelope and error format; money, dates and "
        "versioning rules; authorization policies; integration tests for happy and failure paths. "
        "Not Applicable when no endpoint or contract changed",
    ),
    Gate(4, "Frontend", False, "Not Applicable until a frontend exists"),
    Gate(5, "Release", False, "a human promotion step: never run by ADLC"),
)
_BY_NUMBER = {gate.number: gate for gate in GATES}


@dataclass(frozen=True)
class GateResult:
    number: int
    name: str
    result: str
    evidence: str = ""


@dataclass(frozen=True)
class ReviewReport:
    decision: str  # approve, changes_requested or escalate
    standards: tuple[str, ...] = ()
    spec: tuple[str, ...] = ()
    gates: tuple[GateResult, ...] = ()
    reason: str = ""  # why the driver escalated or changed the decision

    def as_review(self) -> Review:
        """The verdict in the form the loop takes: the decision and the findings per axis."""
        sections = []
        if self.reason:
            sections.append(self.reason)
        for title, items in (("Standards", self.standards), ("Spec", self.spec)):
            if items:
                sections.append(f"## {title}\n" + "\n".join(f"- {item}" for item in items))
        failed = [g for g in self.gates if g.result == FAIL]
        if failed:
            lines = (f"- Gate {g.number} ({g.name}): {g.evidence or 'failed'}" for g in failed)
            sections.append("## Gates\n" + "\n".join(lines))
        return Review(self.decision, "\n\n".join(sections))


def signoff(gate: GateResult, *, model: str, scope: str, on: date) -> str:
    """The line every quality gate ends with: who reviewed, when, what, and the result."""
    return (
        f"Gate {gate.number} ({gate.name}): reviewed by {model} (agent), {on.isoformat()}, "
        f"scope {scope}, result {gate.result}"
    )


def review_prompt(brief: str, diff: str) -> str:
    gate_lines = "\n".join(f"- Gate {g.number} ({g.name}): {g.checks}" for g in GATES if g.run)
    return f"""\
You are the Reviewer. You cannot edit anything. Judge the diff below against the brief.

Review on two axes:
- Standards: does the code follow this repo's documented rules and conventions?
- Spec: does the code do what the brief asked: every acceptance criterion met, nothing extra?

Fill the checklist for each gate below with Pass, Pass with noted exceptions, Fail or Not
Applicable, and give the evidence (a file, a line, a command) for each.
{gate_lines}

Reply with one JSON object in a ```json fence and nothing after it:
{{"decision": "approve" | "changes_requested" | "escalate",
  "standards": ["each open finding, one line"],
  "spec": ["each open finding, one line"],
  "gates": {{"1": {{"result": "...", "evidence": "..."}}, "2": {{...}}, "3": {{...}}}}}}
Approve only when both axes have no open finding and no gate Fails. Escalate when a person must
decide (a contradictory brief, a risk you cannot judge).

## Brief
{brief}

## Diff
{diff}
"""


def _escalate(reason: str) -> ReviewReport:
    return ReviewReport("escalate", reason=reason)


def _items(value: object, axis: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{axis!r} must be a list of strings")
    return tuple(item for item in value if item.strip())


def _gates(raw: object) -> tuple[GateResult, ...]:
    if not isinstance(raw, dict):
        raise ValueError("'gates' must be an object")
    results = []
    for gate in GATES:
        if not gate.run:
            if gate.number == 4:
                results.append(GateResult(4, gate.name, NOT_APPLICABLE, gate.checks))
            continue  # gate 5 is never run, so it is not reported
        entry = raw.get(str(gate.number))
        if not isinstance(entry, dict):
            raise ValueError(f"gate {gate.number} ({gate.name}) was not filled in")
        result, evidence = entry.get("result"), entry.get("evidence", "")
        if result not in RESULTS:
            raise ValueError(f"gate {gate.number}: unknown result {result!r} (one of {RESULTS})")
        results.append(GateResult(gate.number, gate.name, result, str(evidence)))
    return tuple(results)


def _load(text: str) -> dict:
    blocks = _FENCE.findall(text)
    candidates = [blocks[-1]] if blocks else [text[text.find("{") :]]
    for candidate in candidates:
        try:
            data, _ = json.JSONDecoder().raw_decode(candidate.strip())
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("no JSON verdict found in the reply")


def parse_report(text: str) -> ReviewReport:
    """Read the Reviewer's reply. Anything unusable escalates; an approval is only as good as its
    evidence, so open findings or a failed gate turn it into changes-requested."""
    try:
        data = _load(text)
        decision = data.get("decision")
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r} (one of {DECISIONS})")
        standards, spec = (
            _items(data.get("standards"), "standards"),
            _items(data.get("spec"), "spec"),
        )
        if decision == "escalate":
            try:
                gates = _gates(data.get("gates"))
            except ValueError:
                gates = ()
            return ReviewReport(decision, standards, spec, gates)
        gates = _gates(data.get("gates"))
    except ValueError as problem:
        return _escalate(f"The Reviewer's reply could not be used: {problem}.")
    if decision == "approve" and (standards or spec or any(g.result == FAIL for g in gates)):
        return ReviewReport(
            "changes_requested",
            standards,
            spec,
            gates,
            "Approved with open findings or a failed gate: treated as changes requested.",
        )
    return ReviewReport(decision, standards, spec, gates)


def _diff(worktree: Path, base: str) -> str:
    """Everything changed since `base`, new files included (staged, as the loop does)."""
    for args in (("add", "-A"), ("diff", "--cached", base)):
        done = subprocess.run(
            ["git", *args], cwd=worktree, capture_output=True, text=True, encoding="utf-8"
        )
        if done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def make_reviewer(
    runner: AgentRunner,
    *,
    brief: str,
    method: str = "",
    timeout: float | None = None,
    on_report: Callable[[ReviewReport], None] | None = None,
) -> Callable[[Path, str], Review]:
    """A `Reviewer` for the loop: (worktree, base commit) -> verdict. `on_report` receives the
    full report (gates included) for the run log."""

    def review(worktree: Path, base: str) -> Review:
        diff = _diff(worktree, base)
        if not diff.strip():
            report = _escalate("There are no changes to review since the base commit.")
        else:
            options = {"timeout": timeout} if timeout is not None else {}
            result = runner.run(
                AgentRequest(
                    role=Role.REVIEWER,
                    prompt=review_prompt(brief, diff),
                    cwd=worktree,
                    method=method or None,
                    allowed_tools=READ_ONLY,
                    disallowed_tools=NO_EDITS,
                    **options,
                )
            )
            if result.status != "ok":
                report = _escalate(
                    f"The Reviewer call did not finish ({result.status}): {result.detail}"
                )
            else:
                report = parse_report(result.text)
        if on_report:
            on_report(report)
        return report.as_review()

    return review
