"""`adlc run`: carry one task from the board to a pull request (FR-6, FR-8, FR-9, FR-11).

This joins the parts that were built and tested on their own, in the order the PRD gives: select
the task, check the baseline and the usage window, cut the worktree, let the Tester write tests and
prove them red, lock them, run the Ralph loop with the Reviewer, then hand the result to the PR
step. Every call to a model goes through `GuardedRunner`. Anything that is not a clean success
ends in a pause or an escalation, never in a quiet failure.

It runs one task at a time, and it does not yet continue a paused or interrupted run: that task
stays `IN_PROGRESS` until `adlc reset` (continuing is a later item).
"""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from adlc.baseline import load_baseline
from adlc.commands import CommandError, locations, update_row
from adlc.config import Settings, load_config
from adlc.dod import DEFAULT_TIMEOUT_SECONDS
from adlc.escalation import (
    Escalation,
    Facts,
    Trigger,
    escalation_for,
    from_outcome,
    raise_escalation,
)
from adlc.gitwork import commit, create_worktree, remove_worktree
from adlc.guardrails import GuardedRunner, make_scrubber
from adlc.loop import Stop, run_loop
from adlc.plan import base_prompt, plan_run
from adlc.pr import Delivery, Forge, deliver
from adlc.recovery import can_start, pause_hook, pause_task, record_start
from adlc.review import ReviewReport, make_reviewer
from adlc.runlog import new_run
from adlc.runner import AgentRequest, AgentRunner, Role
from adlc.scope import out_of_scope
from adlc.stages import MethodRefused, resolve, tester_stage
from adlc.states import State
from adlc.tasks import Task, load_tasks, select
from adlc.tdd import changed_paths, lock_tests, out_of_scope_for_tester, red_check

_SPEC_PROBLEM = re.compile(r"(?im)^\s*SPEC PROBLEM:\s*(.+)$")
_LOOP_STAGES = ("implement", "diagnose", "review")


@dataclass(frozen=True)
class RunResult:
    status: str  # merged, waiting, escalated, paused or nothing
    key: str | None
    detail: str = ""
    delivery: Delivery | None = None
    escalations: tuple[Escalation, ...] = ()
    run_dir: Path | None = None


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode != 0:
        raise CommandError(f"git {' '.join(args[:2])} failed: {(done.stderr or '').strip()}")
    return done.stdout.strip()


def _facts(worktree: Path, base: str, settings: Settings, requires_approval: bool) -> Facts:
    """What the diff looks like, for the escalation triggers the PR step evaluates."""
    paths, counts = [], {}
    for line in _git(worktree, "diff", "--numstat", "--no-renames", base).splitlines():
        added, removed, path = line.split("\t", 2)
        if path == "progress.md":
            continue
        paths.append(path)
        counts[path] = (int(added) if added.isdigit() else 0) + (
            int(removed) if removed.isdigit() else 0
        )
    not_tests = set(out_of_scope(paths, [], allowed=settings.tests.paths))
    return Facts(
        requires_approval=requires_approval,
        changed=tuple(paths),
        diff_lines=sum(n for p, n in counts.items() if p in not_tests),
        test_diff_lines=sum(n for p, n in counts.items() if p not in not_tests),
    )


def _escalate(repo, key, found, run_dir, notify) -> RunResult:
    log = run_dir.relative_to(repo).as_posix()
    raise_escalation(repo, key, found, run_log=log, notify=notify)
    first = found[0] if isinstance(found, (list, tuple)) else found
    escalations = tuple(found) if isinstance(found, (list, tuple)) else (found,)
    return RunResult("escalated", key, first.reason, escalations=escalations, run_dir=run_dir)


def next_ready_key(repo: Path) -> str:
    """The key `adlc run` would take with no argument, or an error saying nothing is ready."""
    board, briefs, _ = locations(repo)
    chosen = select(load_tasks(board, briefs))
    if not chosen.ready:
        raise CommandError("no BACKLOG task is ready (dependencies DONE)")
    return chosen.ready[0].key


def render_result(result: RunResult) -> str:
    """One short report of how a run ended, for the person who started it."""
    if result.status == "nothing":
        return f"nothing to run: {result.detail}"
    log = f"\n  log: {result.run_dir.as_posix()}" if result.run_dir else ""
    if result.status == "merged":
        return f"{result.key}: merged. {result.detail}{log}"
    if result.status == "paused":
        return f"{result.key}: paused ({result.detail}); it stays IN_PROGRESS{log}"
    if result.status == "waiting":
        return f"{result.key}: PR open, checks not finished. {result.detail}{log}"
    why = "; ".join(e.reason for e in result.escalations) or result.detail
    pr = f"\n  PR: {result.delivery.pr.url}" if result.delivery and result.delivery.pr else ""
    return f"{result.key}: escalated, now NEEDS_HUMAN: {why}{pr}{log}"


def _choose(repo: Path, key: str | None) -> Task | None:
    board, briefs, _ = locations(repo)
    chosen = select(load_tasks(board, briefs))
    if key is None:
        return chosen.ready[0] if chosen.ready else None
    plan = plan_run(repo, key)  # raises with the reason when the key is not a runnable item
    if not plan.ready:
        raise CommandError(f"{key} cannot run now: {plan.why_not}")
    return next(t for t in chosen.ready if t.key == key)


def _tester_phase(repo, settings, task, made, base, guarded, resolutions, run_dir, bash, timeout):
    """Run the Tester and prove its tests red. Returns (lock, None) or (None, Escalation/result)."""
    plan = plan_run(repo, task.key)
    result = guarded.run(
        AgentRequest(
            Role.TESTER,
            plan.tester_prompt,
            made.path,
            method=resolutions[tester_stage(task.brief.type)].text or None,
        )
    )
    if result.status == "rate_limited":
        limit = result.rate_limit
        pause_task(
            repo, task.key, resets_at=limit.resets_at if limit else None, detail=result.detail
        )
        return None, "paused"
    if result.status != "ok":
        reason = f"the Tester call did not finish ({result.status}): {result.detail}"
        return None, escalation_for(Trigger.LOOP_EXHAUSTED, reason)
    problem = _SPEC_PROBLEM.search(result.text)
    if problem:
        return None, escalation_for(Trigger.SPEC_PROBLEM, problem.group(1).strip())
    changes = changed_paths(made.path, base)
    if not changes:
        return None, escalation_for(Trigger.TEST_INTEGRITY, "the Tester wrote no tests")
    bad = out_of_scope_for_tester(changes, settings) + [p for p, s in changes.items() if s == "D"]
    if bad:
        reason = "the Tester changed files that are not new or edited tests"
        return None, escalation_for(Trigger.TEST_INTEGRITY, reason, tuple(dict.fromkeys(bad)))
    files = sorted(changes)
    try:
        red = red_check(made.path, settings, run_dir / "tester", files, bash=bash, timeout=timeout)
    except CommandError as exc:
        return None, escalation_for(Trigger.TEST_INTEGRITY, f"the red check could not run: {exc}")
    if not red.ok:
        passing, broken = ", ".join(red.passing) or "none", ", ".join(red.broken) or "none"
        reason = (
            "the new tests do not fail for a real reason on the unmodified code "
            f"(passing: {passing}; broken: {broken})"
        )
        return None, escalation_for(Trigger.TEST_INTEGRITY, reason, (str(red.record),))
    commit(made.path, f"test({task.key.lower()}): failing tests for {task.key}", settings.git)
    return lock_tests(made.path, files, run_dir / "lock"), None


def run_task(
    repo: Path,
    key: str | None = None,
    *,
    runner: AgentRunner,
    forge: Forge,
    skills_root: Path | None = None,
    root: Path | None = None,
    fetch: bool = True,
    max_iterations: int | None = None,
    bash: str | None = None,
    dod_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 30.0,
    max_wait: float = 1800.0,
    now: datetime | None = None,
    notify: Callable[[str, list[Escalation]], None] | None = None,
) -> RunResult:
    """Run `key` (or the best ready task) to a pull request, a pause or an escalation."""
    repo = Path(repo)
    settings = load_config(repo)
    task = _choose(repo, key)
    if task is None:
        return RunResult("nothing", None, "no BACKLOG task is ready (dependencies DONE)")
    key = task.key
    if load_baseline(repo) is None:
        raise CommandError(
            "there is no baseline: run `adlc doctor --baseline` first, because a task cannot run "
            "until the repository's existing failures are understood"
        )
    if not can_start(repo, settings, now, key):
        raise CommandError(
            f"the usage window has no room for another task (limits.max_tasks_per_window is "
            f"{settings.limits.max_tasks_per_window}); try again when it resets"
        )
    try:
        resolutions = {
            stage: resolve(settings, repo, stage, skills_root=skills_root)
            for stage in {tester_stage(task.brief.type), *_LOOP_STAGES}
        }
    except MethodRefused as exc:
        raise CommandError(str(exc)) from exc

    guarded = GuardedRunner(runner, settings)
    made = create_worktree(
        repo, key, task.brief.type, task.title, settings.git, root=root, fetch=fetch
    )
    try:
        update_row(repo, key, State.IN_PROGRESS, f"Started on {made.branch}")
    except Exception:
        remove_worktree(repo, key, force=True, root=made.path.parent)
        raise
    record_start(repo, key, now)
    _, run_dir = new_run(repo, key)
    base = _git(made.path, "rev-parse", "HEAD")

    lock, stopped = _tester_phase(
        repo, settings, task, made, base, guarded, resolutions, run_dir, bash, dod_timeout
    )
    if stopped == "paused":
        return RunResult("paused", key, "a usage limit paused the Tester", run_dir=run_dir)
    if stopped is not None:
        return _escalate(repo, key, [stopped], run_dir, notify)

    briefs = locations(repo)[1]
    shared = base_prompt(repo, briefs, task)
    reports: list[ReviewReport] = []
    outcome = run_loop(
        worktree=made.path,
        base=base,
        settings=settings,
        runner=guarded,
        review=make_reviewer(
            guarded,
            brief=shared,
            method=resolutions["review"].text,
            on_report=reports.append,
        ),
        lock=lock,
        prompt=shared,
        run_dir=run_dir,
        baseline=load_baseline(repo),
        method=resolutions["implement"].text or None,
        diagnose_method=resolutions["diagnose"].text or None,
        key=key,
        methods={s: resolutions[s].record() for s in _LOOP_STAGES},
        scrub=make_scrubber(settings),
        max_iterations=max_iterations,
        bash=bash,
        dod_timeout=dod_timeout,
        on_pause=pause_hook(repo, key, now),
        clock=clock,
    )
    if outcome.stop is Stop.PAUSED:
        return RunResult("paused", key, outcome.detail, run_dir=run_dir)
    if not outcome.success:
        return _escalate(repo, key, [from_outcome(outcome)], run_dir, notify)

    delivery = deliver(
        repo,
        key,
        made.path,
        runner=guarded,
        forge=forge,
        report=reports[-1],
        dod_green=True,
        model=settings.models.coder or "default",
        run_log=run_dir.relative_to(repo).as_posix(),
        facts=_facts(made.path, base, settings, task.brief.requires_approval),
        skills_root=skills_root,
        sleep=sleep,
        clock=clock,
        poll_seconds=poll_seconds,
        max_wait=max_wait,
        notify=notify,
    )
    detail = delivery.pr.url if delivery.pr else ""
    return RunResult(
        delivery.status,
        key,
        detail,
        delivery=delivery,
        escalations=delivery.escalations,
        run_dir=run_dir,
    )
