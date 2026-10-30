"""The escalation engine and the `NEEDS_HUMAN` queue (PRD 8.4a, FR-23 to FR-27).

The orchestrator, never an agent, decides when a person must look. `evaluate` turns facts about a
task into the triggers that fired, `from_outcome` does the same for a loop that stopped, and
`raise_escalation` stops the task: it sets the board to `NEEDS_HUMAN` and keeps a record under
`.adlc/escalations/`. The person answers with approve, retry, edit or reject. An approval closes the
record, so `merge_allowed` is true again; the PR step (ADLC-021) must ask it before it merges.
`log.jsonl` is the append-only history that `report` reads.
"""

import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from adlc import refine as refine_module
from adlc.board import Board
from adlc.commands import CommandError, locations
from adlc.config import Settings
from adlc.loop import LoopOutcome, Stop
from adlc.scope import out_of_scope
from adlc.states import Actor, State, check_transition, parse_state
from adlc.tasks import parse_brief

SCHEMA = 1
STORE = Path(".adlc") / "escalations"
LOG = "log.jsonl"
RESPONSES = ("approve", "retry", "edit", "reject")
_EXEMPT = ("CLAUDE.md",)  # with the board file: the orchestrator's own index edits (FR-4a, FR-18a)


class Trigger(StrEnum):
    LOOP_EXHAUSTED = "loop_exhausted"
    REVIEW_UNRESOLVED = "review_unresolved"
    PROTECTED_AREA = "protected_area"
    SCOPE_SIZE = "scope_size"
    QUALITY_REGRESSION = "quality_regression"
    TEST_INTEGRITY = "test_integrity"
    SPEC_PROBLEM = "spec_problem"
    EXTERNAL_CHECK = "external_check"
    GUARDRAIL = "guardrail"
    TASK_FLAG = "task_flag"


_NEXT_STEP = {
    Trigger.LOOP_EXHAUSTED: "Read the run log, then retry with a note, edit the task or reject it.",
    Trigger.REVIEW_UNRESOLVED: "Read the Reviewer's findings, then approve, retry or reject.",
    Trigger.PROTECTED_AREA: "Check the protected files in the diff; approve only if right.",
    Trigger.SCOPE_SIZE: "Check if the change is too big or strays; consider splitting it.",
    Trigger.QUALITY_REGRESSION: "Look at the gate that newly fails; retry with a note or reject.",
    Trigger.TEST_INTEGRITY: "Decide whether the Tester's test was wrong; edit the task or reject.",
    Trigger.SPEC_PROBLEM: "Fix the acceptance criteria: `adlc retry KEY --refine`.",
    Trigger.EXTERNAL_CHECK: "Look at the failed check on the PR; retry with a note or reject.",
    Trigger.GUARDRAIL: "Look at the action AGENTS.md forbids; approve only if it was harmless.",
    Trigger.TASK_FLAG: "The task asked for a person to sign off; review it and approve.",
}
_STOP_TRIGGER = {
    Stop.MAX_ITERATIONS: Trigger.LOOP_EXHAUSTED,
    Stop.MAX_MINUTES: Trigger.LOOP_EXHAUSTED,
    Stop.STUCK: Trigger.LOOP_EXHAUSTED,
    Stop.REGRESSION: Trigger.LOOP_EXHAUSTED,
    Stop.REVIEW_ROUNDS: Trigger.REVIEW_UNRESOLVED,
    Stop.REVIEW_ESCALATED: Trigger.REVIEW_UNRESOLVED,
    Stop.GUARDRAIL: Trigger.PROTECTED_AREA,
    Stop.TEST_PROTECTION: Trigger.TEST_INTEGRITY,
    Stop.TEST_PROPOSAL: Trigger.TEST_INTEGRITY,
}


@dataclass(frozen=True)
class Escalation:
    trigger: Trigger
    reason: str
    evidence: tuple[str, ...] = ()  # files, gates or links that show why
    next_step: str = ""


@dataclass(frozen=True)
class Facts:
    """What the orchestrator knows about a task at a checkpoint (an iteration, or pre-merge)."""

    requires_approval: bool = False
    changed: tuple[str, ...] = ()  # paths changed so far
    diff_lines: int = 0  # production lines changed
    test_diff_lines: int = 0  # test lines changed (they count toward the hard ceiling)
    outside_task: tuple[str, ...] = ()  # files the task does not imply
    new_dependencies: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()  # gates that newly fail or findings (advisory, secret)
    test_integrity: str = ""  # why a Tester-written test was changed, removed or not red
    spec_problem: str = ""  # the Tester or Reviewer found the criteria unclear or contradictory
    needs_adr: bool = False  # the task needs an ADR it did not declare
    external: str = ""  # remote CI failed or branch protection blocked the merge
    guardrail: tuple[str, ...] = ()  # attempted actions outside AGENTS.md
    extra: dict = field(default_factory=dict)


def regressions_from(verdicts: Iterable[Any]) -> tuple[str, ...]:
    """The commands that fail now and did not fail in the baseline."""
    return tuple(v.name for v in verdicts if v.verdict == "new")


def from_outcome(outcome: LoopOutcome) -> Escalation | None:
    """The escalation for a loop that stopped, or None if it succeeded or only paused."""
    if not outcome.escalate:
        return None
    trigger = _STOP_TRIGGER[outcome.stop]
    evidence = [str(outcome.record)]
    if outcome.findings.strip():
        evidence.append(outcome.findings.strip())
    reason = f"{outcome.stop.value}: {outcome.detail}" if outcome.detail else outcome.stop.value
    return Escalation(trigger, reason, tuple(evidence), _NEXT_STEP[trigger])


def _found(trigger: Trigger, reason: str, evidence: Sequence[str] = ()) -> Escalation:
    return Escalation(trigger, reason, tuple(evidence), _NEXT_STEP[trigger])


def evaluate(facts: Facts, settings: Settings) -> list[Escalation]:
    """Every trigger the facts fire, in the order of the table in PRD 8.4a."""
    found: list[Escalation] = []
    exempt = {settings.board.path, *_EXEMPT}
    touched = out_of_scope([p for p in facts.changed if p not in exempt], settings.protected)
    if touched:
        found.append(_found(Trigger.PROTECTED_AREA, "protected files changed", touched))

    limits = settings.limits
    total = facts.diff_lines + facts.test_diff_lines
    if total > limits.max_diff_lines_hard:
        found.append(
            _found(
                Trigger.SCOPE_SIZE,
                f"{total} lines changed, over the hard ceiling of {limits.max_diff_lines_hard}",
            )
        )
    elif facts.diff_lines > limits.max_diff_lines:
        found.append(
            _found(
                Trigger.SCOPE_SIZE,
                f"{facts.diff_lines} production lines changed, over the limit of "
                f"{limits.max_diff_lines}",
            )
        )
    if facts.outside_task:
        found.append(
            _found(
                Trigger.SCOPE_SIZE, "files changed that the task does not imply", facts.outside_task
            )
        )
    if facts.new_dependencies:
        found.append(
            _found(Trigger.SCOPE_SIZE, "new top-level dependencies", facts.new_dependencies)
        )
    if facts.regressions:
        found.append(
            _found(
                Trigger.QUALITY_REGRESSION,
                "a gate newly fails: " + ", ".join(facts.regressions),
                facts.regressions,
            )
        )
    if facts.test_integrity:
        found.append(_found(Trigger.TEST_INTEGRITY, facts.test_integrity))
    if facts.spec_problem:
        found.append(_found(Trigger.SPEC_PROBLEM, facts.spec_problem))
    if facts.needs_adr:
        found.append(_found(Trigger.SPEC_PROBLEM, "the task needs an ADR it did not declare"))
    if facts.external:
        found.append(_found(Trigger.EXTERNAL_CHECK, facts.external))
    if facts.guardrail:
        found.append(
            _found(
                Trigger.GUARDRAIL, "an action outside the AGENTS.md permissions", facts.guardrail
            )
        )
    if facts.requires_approval:
        found.append(_found(Trigger.TASK_FLAG, "the task is tagged requires_approval"))
    return found


# --- the queue ----------------------------------------------------------------------------------


def _store(repo: Path) -> Path:
    return Path(repo) / STORE


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(repo: Path, event: dict[str, Any]) -> None:
    path = _store(repo) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": _now(), **event}, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def _record_path(repo: Path, key: str) -> Path:
    return _store(repo) / f"{key}.json"


def _load(repo: Path, key: str) -> dict[str, Any] | None:
    path = _record_path(repo, key)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CommandError(f"the escalation record {path} is damaged ({exc})") from exc
    return data if isinstance(data, dict) else None


def list_open(repo: Path) -> list[dict[str, Any]]:
    """Every escalation waiting for a person, oldest key first."""
    records = []
    for path in sorted(_store(repo).glob("*.json")):
        record = _load(repo, path.stem)
        if record:
            records.append(record)
    return records


def merge_allowed(repo: Path, key: str) -> bool:
    """False while the task has an escalation no person has answered."""
    return _load(repo, key) is None


def _task_type(repo: Path, key: str) -> str:
    try:
        _, briefs, _ = locations(repo)
        return parse_brief((briefs / f"{key}.md").read_text(encoding="utf-8"), key).type
    except (OSError, ValueError, KeyError):
        return "unknown"


def _row_state(board: Board, key: str) -> State:
    for row in board.rows():
        if row.key == key:
            cells = {k.lower(): v for k, v in row.cells.items()}
            return parse_state(cells.get("status", ""))
    raise CommandError(f"{key} is not on the board")


def _set(board: Board, key: str, status: State | None, notes: str) -> None:
    with board.lock():
        if status is not None:
            board.set_cell(key, "Status", status.value)
        board.set_cell(key, "Notes", notes)


def raise_escalation(
    repo: Path,
    key: str,
    escalations: Escalation | Sequence[Escalation],
    *,
    run_log: str | None = None,
    notify: Callable[[str, list[Escalation]], None] | None = None,
) -> None:
    """Stop `key`: set it to NEEDS_HUMAN, keep the evidence and tell the person (FR-23, FR-25)."""
    found = [escalations] if isinstance(escalations, Escalation) else list(escalations)
    if not found:
        raise CommandError("an escalation needs at least one trigger")
    board_path, _, _ = locations(repo)
    board = Board(board_path)
    state = _row_state(board, key)
    if state is not State.NEEDS_HUMAN:
        check_transition(state, State.NEEDS_HUMAN, Actor.ORCHESTRATOR)

    record = _load(repo, key) or {
        "schema": SCHEMA,
        "key": key,
        "type": _task_type(repo, key),
        "raised_at": _now(),
        "run_log": run_log,
        "approved": False,
        "escalations": [],
    }
    record["escalations"] += [
        {
            "trigger": e.trigger.value,
            "reason": e.reason,
            "evidence": list(e.evidence),
            "next_step": e.next_step,
        }
        for e in found
    ]
    record["run_log"] = run_log or record.get("run_log")
    _write(_record_path(repo, key), json.dumps(record, indent=2) + "\n")

    first = found[0]
    more = f" (+{len(found) - 1} more)" if len(found) > 1 else ""
    link = f" Log: {record['run_log']}" if record.get("run_log") else ""
    notes = f"NEEDS_HUMAN: {first.trigger.value}: {_one_line(first.reason)}{more}.{link}"
    _set(board, key, None if state is State.NEEDS_HUMAN else State.NEEDS_HUMAN, notes)
    for e in found:
        _log(
            repo,
            {"event": "raised", "key": key, "trigger": e.trigger.value, "type": record["type"]},
        )
    if notify:
        notify(key, found)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _answer(repo: Path, key: str, response: str, *, status: State | None, notes: str):
    """Close the open record of `key` with the person's `response`; returns the record."""
    record = _load(repo, key)
    if record is None:
        raise CommandError(f"{key} is not waiting for a person (no open escalation)")
    board_path, _, _ = locations(repo)
    board = Board(board_path)
    if status is not None:
        check_transition(_row_state(board, key), status, Actor.HUMAN)
    _set(board, key, status, notes)
    _record_path(repo, key).unlink()
    for e in record["escalations"]:
        _log(
            repo,
            {"event": response, "key": key, "trigger": e["trigger"], "type": record["type"]},
        )
    return record


def approve(repo: Path, key: str) -> None:
    """Allow the merge as it is. The task stays NEEDS_HUMAN until the PR step merges it."""
    _answer(repo, key, "approve", status=None, notes="NEEDS_HUMAN: approved; ready to merge.")


def retry(
    repo: Path,
    key: str,
    *,
    note: str | None = None,
    refine: bool = False,
    refine_fn: Callable[[Path, str], object] | None = None,
) -> None:
    """Run the task again with a fresh budget; the note is carried into the next run (FR-24)."""
    record = _load(repo, key)
    if refine and record is not None:
        if not any(e["trigger"] == Trigger.SPEC_PROBLEM.value for e in record["escalations"]):
            raise CommandError(f"{key} did not stop on a spec problem; --refine does not apply")
    _answer(
        repo,
        key,
        "retry",
        status=State.DRAFT if refine else State.BACKLOG,
        notes="Retry requested" + (f": {_one_line(note)}" if note else "."),
    )
    if note:
        path = _store(repo) / f"{key}.note.md"
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        _write(path, old + f"## {_now()}\n{note.strip()}\n\n")
    if refine:
        (refine_fn or refine_module.refine)(repo, key)


def edit(repo: Path, key: str) -> Path:
    """Send the task back to DRAFT so its brief can be changed; returns the brief's path."""
    _answer(repo, key, "edit", status=State.DRAFT, notes="Being edited by a person.")
    _, briefs, _ = locations(repo)
    return briefs / f"{key}.md"


def reject(repo: Path, key: str, *, reason: str = "") -> None:
    """Give the task up: FAILED, with no draft PR (FR-27)."""
    why = f": {_one_line(reason)}" if reason else "."
    _answer(repo, key, "reject", status=State.FAILED, notes=f"Rejected by a person{why}")


# --- the report ---------------------------------------------------------------------------------

_COUNTED = {"approve": "approved", "retry": "retried", "edit": "edited", "reject": "rejected"}


def _events(repo: Path) -> list[dict[str, Any]]:
    path = _store(repo) / LOG
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # a half-written last line must not hide the rest
    return events


def report(repo: Path) -> dict[str, Any]:
    """Escalation rates by trigger and task type, and how often the person overrode (FR-26).

    Approving unchanged is agreement; retry, edit and reject all override what the agents did.
    """
    by_trigger: dict[str, dict[str, Any]] = {}
    by_type: Counter[str] = Counter()
    for event in _events(repo):
        row = by_trigger.setdefault(
            event["trigger"],
            dict.fromkeys(("raised", "approved", "retried", "edited", "rejected"), 0),
        )
        if event["event"] == "raised":
            row["raised"] += 1
            by_type[event.get("type", "unknown")] += 1
        elif event["event"] in _COUNTED:
            row[_COUNTED[event["event"]]] += 1
    resolved = overridden = 0
    for row in by_trigger.values():
        row["overridden"] = row["retried"] + row["edited"] + row["rejected"]
        done = row["approved"] + row["overridden"]
        row["pending"] = row["raised"] - done
        row["override_rate"] = row["overridden"] / done if done else 0.0
        resolved += done
        overridden += row["overridden"]
    return {
        "by_trigger": by_trigger,
        "by_type": dict(by_type),
        "resolved": resolved,
        "overridden": overridden,
    }


def render_report(data: dict[str, Any]) -> str:
    if not data["by_trigger"]:
        return "no escalations yet\n"
    lines = [
        f"{'TRIGGER':<22}{'RAISED':<8}{'APPROVED':<9}{'RETRIED':<8}{'EDITED':<7}"
        f"{'REJECTED':<9}{'PENDING':<8}OVERRIDE RATE"
    ]
    for name, r in sorted(data["by_trigger"].items()):
        lines.append(
            f"{name:<22}{r['raised']:<8}{r['approved']:<9}{r['retried']:<8}{r['edited']:<7}"
            f"{r['rejected']:<9}{r['pending']:<8}{r['override_rate']:.0%}"
        )
    types = ", ".join(f"{k}: {v}" for k, v in sorted(data["by_type"].items()))
    lines += [
        "",
        f"by task type: {types}",
        f"{data['resolved']} resolved, {data['overridden']} overridden by a person; "
        "a trigger that is always 'approved unchanged' is a candidate to relax",
    ]
    return "\n".join(lines) + "\n"
