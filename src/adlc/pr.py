"""The PR step: push, open the PR, wait for the remote checks, merge and clean up (FR-11, FR-12).

The PR text is written by the PR-writer agent with the pinned `pr` method; everything else is
code. The driver adds the parts that must not depend on a model (the requirement, the gate
checklist with sign-off lines, the log links) and fills in any required section the writer left
out. Merging needs every condition at once (`merge_blockers`); a red check or an open trigger
escalates instead. Nothing is deployed (FR-12a).

The code host sits behind `Forge`: `GhCli` is the real one, `adlc.fake_forge` the test one.
"""

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Protocol

from adlc.board import Board
from adlc.commands import CommandError, locations
from adlc.config import Settings, load_config
from adlc.escalation import (
    Escalation,
    Facts,
    Trigger,
    evaluate,
    merge_allowed,
    raise_escalation,
    task_state,
)
from adlc.gitwork import _git, remove_worktree
from adlc.review import NO_EDITS, READ_ONLY, ReviewReport, signoff
from adlc.runner import AgentRequest, AgentRunner, Role
from adlc.stages import MethodRefused, resolve
from adlc.states import Actor, State, check_transition
from adlc.tasks import parse_brief
from adlc.trust import record_hand_merge

PASS, FAIL, PENDING = "pass", "fail", "pending"
REQUIRED_SECTIONS = ("Summary", "Impacted modules", "Tests", "Risks")
TEMPLATES = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
    "PULL_REQUEST_TEMPLATE.md",
)
STORE = Path(".adlc") / "prs"
NEEDS_HUMAN_LABEL = "needs-human"
_GH_METHOD = {"squash": "--squash", "rebase": "--rebase", "merge": "--merge"}
_BUCKET = {"pass": PASS, "skipping": PASS, "fail": FAIL, "cancel": FAIL, "pending": PENDING}


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str


@dataclass(frozen=True)
class Check:
    name: str
    state: str  # pass, fail or pending


class Forge(Protocol):
    def push(self, worktree: Path, branch: str) -> None: ...

    def create_pr(
        self, worktree: Path, *, base: str, branch: str, title: str, body: str
    ) -> PullRequest: ...

    def checks(self, pr: PullRequest) -> list[Check]: ...

    def mark_needs_human(self, pr: PullRequest) -> None: ...

    def merge(self, pr: PullRequest, method: str) -> None: ...

    def delete_remote_branch(self, repo: Path, branch: str) -> None: ...


Run = Callable[..., subprocess.CompletedProcess]


def _run(args: Sequence[str], cwd: Path | None = None, text_in: str | None = None):
    return subprocess.run(
        list(args), cwd=cwd, input=text_in, capture_output=True, text=True, encoding="utf-8"
    )


class GhCli:
    """The code host through `git` and the GitHub CLI; `run` can be replaced to watch the calls."""

    def __init__(self, run: Run | None = None, cwd: Path | None = None):
        self._run = run or _run
        self.cwd = cwd

    def _do(self, args: Sequence[str], *, cwd: Path | None = None, text_in: str | None = None):
        return self._run(list(args), cwd=cwd or self.cwd, text_in=text_in)

    def _must(self, args: Sequence[str], **kw) -> str:
        done = self._do(args, **kw)
        if done.returncode != 0:
            raise CommandError(f"`{' '.join(args[:3])}` failed: {(done.stderr or '').strip()}")
        return done.stdout or ""

    def push(self, worktree: Path, branch: str) -> None:
        self._must(["git", "push", "-u", "origin", branch], cwd=worktree)

    def create_pr(
        self, worktree: Path, *, base: str, branch: str, title: str, body: str
    ) -> PullRequest:
        args = ["gh", "pr", "create", "--base", base, "--head", branch, "--title", title]
        out = self._must([*args, "--body-file", "-"], cwd=worktree, text_in=body)
        url = out.strip().splitlines()[-1].strip()
        try:
            return PullRequest(int(url.rstrip("/").rsplit("/", 1)[1]), url, branch)
        except (IndexError, ValueError):
            raise CommandError(f"could not read the PR number from {url!r}") from None

    def checks(self, pr: PullRequest) -> list[Check]:
        # gh exits non-zero while checks are pending or failed but still prints them
        done = self._do(["gh", "pr", "checks", str(pr.number), "--json", "name,bucket"])
        text = (done.stdout or "").strip()
        if not text:
            return []  # "no checks reported" yet
        try:
            rows = json.loads(text)
        except ValueError as exc:
            raise CommandError(f"could not read the checks of PR {pr.number}: {exc}") from exc
        return [Check(r["name"], _BUCKET.get(r.get("bucket", ""), PENDING)) for r in rows]

    def mark_needs_human(self, pr: PullRequest) -> None:
        """Best effort: the escalation record, not the label, is what stops the merge."""
        number = str(pr.number)
        if self._do(["gh", "pr", "edit", number, "--add-label", NEEDS_HUMAN_LABEL]).returncode:
            self._do(["gh", "label", "create", NEEDS_HUMAN_LABEL, "--color", "D93F0B"])
            self._do(["gh", "pr", "edit", number, "--add-label", NEEDS_HUMAN_LABEL])
        self._do(["gh", "pr", "ready", number, "--undo"])

    def merge(self, pr: PullRequest, method: str) -> None:
        # No --delete-branch: git cannot delete a branch that a worktree has checked out.
        self._must(["gh", "pr", "merge", str(pr.number), _GH_METHOD[method]])

    def delete_remote_branch(self, repo: Path, branch: str) -> None:
        self._do(["git", "push", "origin", "--delete", branch], cwd=repo)  # GitHub may have done it


# --- the merge conditions -----------------------------------------------------------------------


def merge_blockers(
    *,
    review: str,
    dod_green: bool,
    checks: Sequence[Check],
    required: Sequence[str],
    open_escalation: bool,
    auto_merge: bool,
    approved: bool,
) -> list[str]:
    """Everything that stops a merge now; an empty list means every condition holds (FR-12)."""
    found = []
    if review != "approve":
        found.append(f"the Reviewer has not approved (decision: {review or 'none'})")
    if not dod_green:
        found.append("the Definition of Done is not green")
    reported = {c.name for c in checks}
    for check in checks:
        if check.state == FAIL:
            found.append(f"remote check {check.name!r} failed")
        elif check.state != PASS:
            found.append(f"remote check {check.name!r} is still running")
    for name in required:
        if name not in reported:
            found.append(f"required check {name!r} has not reported")
    if open_escalation:
        found.append("an escalation is open and no person has approved it")
    if not auto_merge and not approved:
        found.append("auto_merge is off and no person has approved")
    return found


# --- the body -----------------------------------------------------------------------------------


def read_template(repo: Path) -> str | None:
    for name in TEMPLATES:
        path = Path(repo) / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def _task(repo: Path, key: str):
    board_path, briefs, rel = locations(repo)
    brief = parse_brief((briefs / f"{key}.md").read_text(encoding="utf-8"), key)
    row = next((r for r in Board(board_path).rows() if r.key == key), None)
    cells = {k.lower(): v for k, v in row.cells.items()} if row else {}
    return brief, cells.get("title", key), f"{rel}/{key}.md"


def _base_ref(worktree: Path, base: str) -> str:
    remote = f"origin/{base}"
    has = _git(worktree, "rev-parse", "--verify", "-q", remote, check=False).returncode == 0
    return remote if has else base


def _facts_text(worktree: Path, base: str) -> str:
    ref = _base_ref(worktree, base)
    stat = _git(worktree, "diff", "--stat", f"{ref}...HEAD", check=False).stdout.strip()
    log = _git(worktree, "log", "--format=- %s", f"{ref}..HEAD", check=False).stdout.strip()
    return f"Commits:\n{log or '(none)'}\n\nDiff stat:\n{stat or '(none)'}"


def _writer_prompt(key, title, criteria, facts, template) -> str:
    wanted = (
        "Fill in the repository's PR template below, keeping its headings.\n\n" + template
        if template
        else "Write these sections, each under a `## ` heading: "
        + ", ".join(REQUIRED_SECTIONS)
        + ". Under Tests, say what ran and with what result."
    )
    return (
        f"Write the pull request description for work item {key}: {title}.\n\n"
        "Acceptance criteria:\n"
        + "\n".join(f"- {c}" for c in criteria)
        + f"\n\n{facts}\n\n{wanted}\n\n"
        "Do not write the gate checklist or the log links: the tool adds them. State only what "
        "you can see in the repository and the facts above; say what you did not verify."
    )


def build_body(
    repo: Path,
    key: str,
    worktree: Path,
    settings: Settings,
    *,
    runner: AgentRunner,
    report: ReviewReport,
    model: str,
    run_log: str | None = None,
    template: str | None = None,
    skills_root: Path | None = None,
    today: date | None = None,
) -> str:
    """The PR description: the writer's text, then the parts the tool adds itself."""
    brief, title, brief_path = _task(repo, key)
    facts = _facts_text(worktree, settings.git.base_branch)
    try:
        method = resolve(settings, repo, "deliver", skills_root=skills_root).text or None
    except MethodRefused:
        method = None  # a refused method is never run; the deterministic body below still works
    result = runner.run(
        AgentRequest(
            role=Role.PR_WRITER,
            prompt=_writer_prompt(key, title, brief.criteria, facts, template),
            cwd=worktree,
            method=method,
            allowed_tools=READ_ONLY,
            disallowed_tools=NO_EDITS,
        )
    )
    text = result.text.strip() if result.status == "ok" else ""
    if not text:
        text = (
            f"## Summary\n(The PR writer did not produce a description: {result.detail}.)\n{facts}"
        )
    if not template:
        for section in REQUIRED_SECTIONS:
            if f"## {section}".lower() not in text.lower():
                text += (
                    f"\n\n## {section}\n_Not stated by the PR writer; the reviewer should check._"
                )

    on = today or date.today()
    lines = [
        f"- {signoff(g, model=model, scope=f'{key} branch diff', on=on)}" for g in report.gates
    ]
    requirement = [f"## Requirement\n- Work item: {key} - {title}", f"- Brief: `{brief_path}`"]
    requirement += [f"- {c}" for c in brief.criteria]
    logs = ["## Logs"]
    if run_log:
        logs += [f"- Run log: `{run_log}`", f"- Summary: `{run_log}/summary.md`"]
    else:
        logs.append("- (no run log)")
    gates = ["## Gate checklist", *(lines or ["- (no gate results)"])]
    return "\n\n".join([text, "\n".join(requirement), "\n".join(gates), "\n".join(logs)]) + "\n"


# --- deliver ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Delivery:
    status: str  # merged, waiting (checks not finished) or escalated
    pr: PullRequest | None = None
    escalations: tuple[Escalation, ...] = ()
    blockers: tuple[str, ...] = field(default_factory=tuple)


def _saved(repo: Path, key: str) -> PullRequest | None:
    path = Path(repo) / STORE / f"{key}.json"
    try:
        return PullRequest(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _save(repo: Path, key: str, pr: PullRequest) -> None:
    path = Path(repo) / STORE / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pr.__dict__), encoding="utf-8")


def _note(repo: Path, key: str, state: State | None, notes: str, actor=Actor.ORCHESTRATOR) -> None:
    board_path, _, _ = locations(repo)
    board = Board(board_path)
    current = task_state(repo, key)
    if state is not None and state is not current:
        check_transition(current, state, actor)
    with board.lock():
        if state is not None and state is not current:
            board.set_cell(key, "Status", state.value)
        board.set_cell(key, "Notes", " ".join(notes.split()))


def deliver(
    repo: Path,
    key: str,
    worktree: Path,
    *,
    runner: AgentRunner,
    forge: Forge,
    report: ReviewReport,
    dod_green: bool,
    model: str,
    run_log: str | None = None,
    facts: Facts | None = None,
    skills_root: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_seconds: float = 30.0,
    max_wait: float = 1800.0,
    notify: Callable[[str, list[Escalation]], None] | None = None,
) -> Delivery:
    """Open the PR for a finished task and merge it when every condition holds.

    Safe to call again: an existing PR is reused, and a task a person has approved is not asked
    again (its triggers were accepted) though a red remote check still stops it.
    """
    repo, worktree = Path(repo), Path(worktree)
    settings = load_config(repo)
    merge = settings.merge
    state = task_state(repo, key)
    approved = state is State.NEEDS_HUMAN and merge_allowed(repo, key)

    early = merge_blockers(
        review=report.decision,
        dod_green=dod_green,
        checks=(),
        required=(),
        open_escalation=False,
        auto_merge=True,
        approved=True,
    )
    if early:
        raise CommandError(f"{key} is not ready for a PR: " + "; ".join(early))

    pr = _saved(repo, key)
    if pr is None:
        body = build_body(
            repo,
            key,
            worktree,
            settings,
            runner=runner,
            report=report,
            model=model,
            run_log=run_log,
            template=read_template(repo),
            skills_root=skills_root,
        )
        branch = _git(worktree, "branch", "--show-current").stdout.strip()
        title = _git(worktree, "log", "-1", "--format=%s").stdout.strip()
        forge.push(worktree, branch)
        pr = forge.create_pr(
            worktree, base=settings.git.base_branch, branch=branch, title=title, body=body
        )
        _save(repo, key, pr)
        link = f"PR #{pr.number}: {pr.url}." + (f" Log: {run_log}" if run_log else "")
        _note(repo, key, State.IN_REVIEW if state is State.IN_PROGRESS else None, link)

    def stop(found: list[Escalation]) -> Delivery:
        raise_escalation(repo, key, found, run_log=run_log, notify=notify)
        forge.mark_needs_human(pr)
        return Delivery("escalated", pr, tuple(found))

    if not approved:
        brief, _, _ = _task(repo, key)
        found = evaluate(
            replace(facts or Facts(), requires_approval=brief.requires_approval), settings
        )
        if not merge.auto_merge and not any(e.trigger is Trigger.TASK_FLAG for e in found):
            found.append(
                Escalation(
                    Trigger.TASK_FLAG,
                    "auto_merge is off: every task needs a person's approval",
                    (),
                    "Review the PR, then `adlc approve`.",
                )
            )
        if found:
            return stop(found)

    deadline = clock() + max_wait
    while True:
        checks = tuple(forge.checks(pr))
        failed = [c.name for c in checks if c.state == FAIL]
        if failed:
            return stop(
                [
                    Escalation(
                        Trigger.EXTERNAL_CHECK,
                        "remote check(s) failed: " + ", ".join(failed),
                        tuple(failed),
                        "Open the failed check on the PR, then retry with a note or reject.",
                    )
                ]
            )
        reported = {c.name for c in checks}
        running = any(c.state != PASS for c in checks)
        if not running and all(name in reported for name in merge.required_checks):
            break
        if clock() >= deadline:
            return Delivery("waiting", pr)
        sleep(poll_seconds)

    blockers = merge_blockers(
        review=report.decision,
        dod_green=dod_green,
        checks=checks,
        required=merge.required_checks,
        open_escalation=not merge_allowed(repo, key),
        auto_merge=merge.auto_merge,
        approved=approved,
    )
    if blockers:
        return Delivery("waiting", pr, blockers=tuple(blockers))

    try:
        forge.merge(pr, merge.method)
    except CommandError as exc:
        return stop(
            [
                Escalation(
                    Trigger.EXTERNAL_CHECK,
                    f"the merge was refused: {exc}",
                    (),
                    "Branch protection blocked the merge; see the PR.",
                )
            ]
        )
    forge.delete_remote_branch(repo, pr.branch)
    # A squash or rebase merge leaves the branch unmerged as far as git can tell, so force it.
    remove_worktree(repo, key, force=True, root=worktree.parent)
    done = f"Merged PR #{pr.number}: {pr.url}." + (f" Log: {run_log}" if run_log else "")
    _note(repo, key, State.DONE, done, Actor.HUMAN if approved else Actor.ORCHESTRATOR)
    if approved:
        record_hand_merge(repo, key, pr.number, pr.url)  # counts towards `adlc trust`
    (Path(repo) / STORE / f"{key}.json").unlink(missing_ok=True)
    return Delivery("merged", pr)
