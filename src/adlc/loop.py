"""The Ralph loop: fresh-context Coder iterations until the orchestrator says done (FR-9, 8.2a).

Each iteration is a new agent call with the same brief plus carried state (the branch diff,
`progress.md`, the last verification report, any Reviewer findings) and no conversation history.
After every iteration the loop itself runs the Definition of Done and checks the rules; the agent's
completion promise decides nothing. The loop ends in success only when every DoD command passes
and the Reviewer approves. Any other stop except a usage-window pause is for the escalation engine
(ADLC-020) to act on: the outcome carries the reason and the evidence.
"""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from adlc.baseline import Baseline
from adlc.commands import CommandError
from adlc.config import Settings
from adlc.dod import DEFAULT_TIMEOUT_SECONDS
from adlc.gitwork import _git, commit
from adlc.runner import AgentRequest, AgentResult, AgentRunner, Role
from adlc.scope import out_of_scope
from adlc.tdd import Lock, changed_paths, check_locked, proposed_test_change
from adlc.tdd import _write as write_atomic
from adlc.verify import Verification, failure_report, verify

SCHEMA = 1
RECORD = "loop.json"
PROMISE = "<promise>COMPLETE</promise>"
_DIGITS = re.compile(r"\d+")
DECISIONS = ("approve", "changes_requested", "escalate")
DEFAULT_DIFF_CHARS = 20_000
DEFAULT_REPORT_CHARS = 6_000
DEFAULT_PROGRESS_CHARS = 6_000

INSTRUCTIONS = f"""\
## Your job this iteration
Pick the single most important failing item below, fix it, run the quick checks, and stop.
Do not edit the tests the Tester wrote; if one is wrong, describe it under a heading
`## Proposed test change` in progress.md and stop.
Before you finish, update progress.md (what you tried, what failed, what remains).
Print {PROMISE} only if you believe everything is done: it only starts the checks, which decide."""


class Stop(StrEnum):
    SUCCESS = "success"
    MAX_ITERATIONS = "max_iterations"
    MAX_MINUTES = "max_minutes"
    STUCK = "stuck"
    REGRESSION = "regression"
    GUARDRAIL = "guardrail"
    TEST_PROTECTION = "test_protection"
    TEST_PROPOSAL = "test_proposal"
    REVIEW_ROUNDS = "review_rounds"
    REVIEW_ESCALATED = "review_escalated"
    PAUSED = "paused"  # a usage window ran out: not a failure, nothing to escalate


@dataclass(frozen=True)
class Review:
    decision: str  # approve, changes_requested or escalate
    findings: str = ""


Reviewer = Callable[[Path, str], Review]  # (worktree, base commit) -> verdict; ADLC-019 supplies it


@dataclass(frozen=True)
class IterationRecord:
    number: int
    status: str  # how the agent call ended: ok, error or timeout
    promise: bool  # the agent printed the completion promise
    false_claim: bool  # ... and the checks did not pass
    changed: tuple[str, ...]  # paths changed, not counting progress.md
    failing: tuple[str, ...]  # DoD commands with a new failure afterwards
    signature: str
    reverted: bool
    duration: float  # seconds, measured by the runner
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LoopOutcome:
    stop: Stop
    detail: str
    iterations: tuple[IterationRecord, ...]
    review_rounds: int
    findings: str  # the Reviewer's latest findings
    resets_at: datetime | None  # when a paused loop may resume
    record: Path

    @property
    def success(self) -> bool:
        return self.stop is Stop.SUCCESS

    @property
    def escalate(self) -> bool:
        return self.stop not in (Stop.SUCCESS, Stop.PAUSED)


# --- failure signatures -------------------------------------------------------------------------


def failure_signature(outputs: Mapping[str, str], markers: tuple[str, ...] | list[str]) -> str:
    """Name each failing command with the first error line of its output; numbers are blanked.

    "The same failure" is the same command failing on the same first error line, so a changed
    duration or line number does not hide a loop that is going nowhere.
    """
    parts = []
    for name in sorted(outputs):
        lines = [ln.strip() for ln in outputs[name].splitlines() if ln.strip()]
        lowered = [m.lower() for m in markers]
        line = next((ln for ln in lines if any(m in ln.lower() for m in lowered)), None)
        line = line or (lines[0] if lines else "(no output)")
        parts.append(f"{name}: {_DIGITS.sub('#', ' '.join(line.split()))}")
    return " | ".join(parts)


# --- the loop -----------------------------------------------------------------------------------


def _trim(text: str, limit: int, *, keep_end: bool = False) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    if keep_end:
        return f"[trimmed {cut} earlier characters]\n{text[-limit:]}"
    return f"{text[:limit]}\n[diff trimmed: {cut} more characters]"


def _revert(worktree: Path, checkpoint: str) -> None:
    """Put the worktree back exactly as it was at `checkpoint`, dropping new files too."""
    _git(worktree, "reset", "--hard", "-q", checkpoint)
    _git(worktree, "clean", "-fdq")


class _Loop:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.iterations: list[IterationRecord] = []
        self.findings = ""
        self.review_rounds = 0
        self.record = Path(self.run_dir) / RECORD

    # state saved for `loop-status` and for a person reading what happened
    def save(self, stop: Stop | None = None, detail: str = "") -> None:
        document = {
            "schema": SCHEMA,
            "stop": stop.value if stop else None,
            "detail": detail,
            "review_rounds": self.review_rounds,
            "findings": self.findings,
            "iterations": [asdict(i) for i in self.iterations],
        }
        write_atomic(self.record, (json.dumps(document, indent=2) + "\n").encode("utf-8"))

    def finish(self, stop: Stop, detail: str = "", resets_at: datetime | None = None):
        self.save(stop, detail)
        return LoopOutcome(
            stop,
            detail,
            tuple(self.iterations),
            self.review_rounds,
            self.findings,
            resets_at,
            self.record,
        )

    def verify(self, number: int) -> tuple[Verification, dict[str, str]]:
        out_dir = Path(self.run_dir) / f"verify-{number}"
        done = verify(
            self.worktree,
            self.settings,
            out_dir,
            baseline=self.baseline,
            bash=self.bash,
            timeout=self.dod_timeout,
        )
        outputs = {}
        for result, verdict in zip(done.results, done.verdicts, strict=True):
            if verdict.verdict == "new":
                text = ""
                for name in (result.err, result.out):
                    path = out_dir / name
                    if path.is_file():
                        text += path.read_bytes().decode("utf-8", errors="replace") + "\n"
                outputs[result.name] = text
        self.report = failure_report(done, out_dir, self.max_report_chars)
        return done, outputs

    def prompt(self) -> str:
        locked = [f":(exclude,literal){p}" for p in (*self.lock.paths, self.progress_file)]
        diff = _git(self.worktree, "diff", self.base, "--", ".", *locked).stdout
        notes = Path(self.worktree) / self.progress_file
        progress = notes.read_text(encoding="utf-8", errors="replace") if notes.is_file() else ""
        sections = [
            self.brief,
            INSTRUCTIONS,
            "## Branch diff (everything done so far, without the locked tests)\n"
            + (_trim(diff, self.max_diff_chars) or "(no changes yet)"),
            f"## {self.progress_file}\n"
            + (_trim(progress.strip(), self.max_progress_chars, keep_end=True) or "(none yet)"),
            f"## Last verification\n{self.report}",
        ]
        if self.findings:
            sections.append(f"## Reviewer findings to address\n{self.findings}")
        return "\n\n".join(sections)

    def call_agent(self, number: int, deadline: float) -> AgentResult:
        remaining = deadline - self.clock()
        request = AgentRequest(
            Role.CODER,
            self.prompt(),
            Path(self.worktree),
            method=self.method,
            timeout=max(1.0, min(DEFAULT_TIMEOUT_SECONDS, remaining)),
            log_path=Path(self.run_dir) / f"agent-{number}.jsonl",
        )
        return self.runner.run(request)

    def review(self) -> Review:
        verdict = self.reviewer(Path(self.worktree), self.base)
        if verdict.decision not in DECISIONS:
            raise CommandError(
                f"unknown review decision {verdict.decision!r} (expected one of {DECISIONS})"
            )
        return verdict

    def log(
        self, number, result, *, reverted=False, failing=(), signature="", ready=False, changed=()
    ) -> None:
        promise = result.status == "ok" and PROMISE in result.text
        self.iterations.append(
            IterationRecord(
                number,
                result.status,
                promise,
                promise and not ready,
                tuple(changed),
                tuple(failing),
                signature,
                reverted,
                result.duration,
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
        )
        self.save()

    def run(self) -> LoopOutcome:
        worktree = Path(self.worktree)
        if _git(worktree, "status", "--porcelain").stdout.strip():
            raise CommandError(
                f"{worktree} has uncommitted changes; commit the Tester's tests (and anything "
                "else) before the loop starts"
            )
        started = self.clock()
        deadline = started + self.max_minutes * 60
        state, outputs = self.verify(0)
        before = set(outputs)
        previous = ""  # the starting failure is not an iteration's failure
        while True:
            if state.ready:
                verdict = self.review()
                if verdict.decision == "approve":
                    return self.finish(Stop.SUCCESS)
                self.findings = verdict.findings
                if verdict.decision == "escalate":
                    return self.finish(Stop.REVIEW_ESCALATED, "the Reviewer escalated")
                self.review_rounds += 1
                if self.review_rounds > self.max_review_rounds:
                    return self.finish(
                        Stop.REVIEW_ROUNDS,
                        f"the Reviewer still asked for changes after {self.max_review_rounds} "
                        "round(s)",
                    )
            if len(self.iterations) >= self.max_iterations:
                return self.finish(Stop.MAX_ITERATIONS, f"{self.max_iterations} iterations used")
            if self.clock() >= deadline:
                return self.finish(Stop.MAX_MINUTES, f"{self.max_minutes} minutes used")

            number = len(self.iterations) + 1
            checkpoint = _git(worktree, "rev-parse", "HEAD").stdout.strip()
            result = self.call_agent(number, deadline)
            if result.status == "rate_limited":
                _revert(worktree, checkpoint)
                if self.on_pause:
                    self.on_pause(result)
                resets = result.rate_limit.resets_at if result.rate_limit else None
                return self.finish(Stop.PAUSED, result.detail, resets)

            if result.status != "ok":
                _revert(worktree, checkpoint)
                self.log(number, result, reverted=True)
                continue

            changes = changed_paths(worktree, checkpoint)
            code = [p for p in changes if p != self.progress_file]
            tampered = check_locked(worktree, self.lock)
            forbidden = out_of_scope(code, self.settings.protected)
            if tampered or forbidden:
                _revert(worktree, checkpoint)
                self.log(number, result, reverted=True, changed=code)
                if tampered:
                    names = ", ".join(v.path for v in tampered)
                    return self.finish(Stop.TEST_PROTECTION, f"locked test(s) changed: {names}")
                return self.finish(
                    Stop.GUARDRAIL, f"changed outside the allowed paths: {', '.join(forbidden)}"
                )
            notes = worktree / self.progress_file
            proposal = proposed_test_change(
                notes.read_text(encoding="utf-8", errors="replace") if notes.is_file() else ""
            )
            if proposal is not None:
                self.log(number, result, changed=code)
                return self.finish(
                    Stop.TEST_PROPOSAL, f"the Coder proposed a test change: {proposal}"
                )
            if not code:
                self.log(number, result)
                return self.finish(Stop.STUCK, "the iteration made no change")

            state, outputs = self.verify(number)
            failing = set(outputs)
            if len(failing - before) > len(before - failing):
                _revert(worktree, checkpoint)
                self.log(number, result, reverted=True, changed=code, failing=sorted(failing))
                return self.finish(
                    Stop.REGRESSION,
                    f"the iteration broke {', '.join(sorted(failing - before))} and was reverted",
                )
            signature = failure_signature(outputs, self.settings.tests.failure_markers)
            self.log(
                number,
                result,
                failing=sorted(failing),
                signature=signature,
                ready=state.ready,
                changed=code,
            )
            commit(worktree, f"chore(loop): iteration {number}", self.settings.git)
            if signature and signature == previous:
                return self.finish(Stop.STUCK, f"the same failure twice: {signature}")
            before, previous = failing, signature


def run_loop(
    *,
    worktree: Path,
    base: str,
    settings: Settings,
    runner: AgentRunner,
    review: Reviewer,
    lock: Lock,
    prompt: str,
    run_dir: Path,
    baseline: Baseline | None = None,
    method: str | None = None,
    max_iterations: int | None = None,
    bash: str | None = None,
    dod_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    progress_file: str = "progress.md",
    max_diff_chars: int = DEFAULT_DIFF_CHARS,
    max_report_chars: int = DEFAULT_REPORT_CHARS,
    max_progress_chars: int = DEFAULT_PROGRESS_CHARS,
    on_pause: Callable[[AgentResult], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LoopOutcome:
    """Run Coder iterations in `worktree` until success or a stop condition.

    `prompt` is the part every iteration shares (the brief, AGENTS.md, the repo map); `base` is the
    commit the branch started from. The worktree must be clean, with the Tester's tests committed
    and `lock` taken on them. `on_pause` is called when a usage limit pauses the loop (ADLC-022).
    """
    limits = settings.limits
    return _Loop(
        worktree=worktree,
        base=base,
        settings=settings,
        runner=runner,
        reviewer=review,
        lock=lock,
        brief=prompt,
        run_dir=run_dir,
        baseline=baseline,
        method=method,
        max_iterations=max_iterations or limits.max_iterations,
        max_review_rounds=limits.max_review_rounds,
        max_minutes=limits.max_task_minutes,
        bash=bash,
        dod_timeout=dod_timeout,
        progress_file=progress_file,
        max_diff_chars=max_diff_chars,
        max_report_chars=max_report_chars,
        max_progress_chars=max_progress_chars,
        on_pause=on_pause,
        clock=clock,
    ).run()
