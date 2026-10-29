"""The plan for a task run: what `adlc run --dry-run` shows (FR-21).

Everything here only reads: the board, the brief, `AGENTS.md`, the config and the method
registry. It never calls a model, fetches, creates a branch or a worktree, or writes a log, so a
person can check a task's branch and prompts before anything happens. The base prompt built here
is also what a real run hands the loop as the part every iteration shares.
"""

from dataclasses import dataclass
from pathlib import Path

from adlc.commands import CommandError, locations
from adlc.config import Settings, load_config
from adlc.gitwork import branch_name, default_root
from adlc.loop import INSTRUCTIONS
from adlc.stages import MethodRefused, resolve, tester_instructions, tester_stage
from adlc.states import State
from adlc.tasks import Task, load_tasks, select

RULES_FILES = ("AGENTS.md", "CLAUDE.md")
CARRIED = "(The branch diff, progress.md and the last verification are added for each iteration.)"


@dataclass(frozen=True)
class MethodChoice:
    stage: str
    skill: str
    source: str  # skill, fallback, none, or refused
    detail: str = ""  # why a fallback was used, or why the method was refused


@dataclass(frozen=True)
class RunPlan:
    key: str
    title: str
    type: str
    branch: str
    worktree: Path
    base: str
    ready: bool  # the task could be picked now
    why_not: str  # when it could not
    methods: dict[str, MethodChoice]
    tester_prompt: str
    coder_prompt: str


def _body(text: str) -> str:
    """A brief without its front-matter."""
    lines = text.removeprefix("﻿").replace("\r\n", "\n").split("\n")
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                return "\n".join(lines[i + 1 :]).strip("\n")
    return "\n".join(lines).strip("\n")


def base_prompt(repo: Path, briefs: Path, task: Task) -> str:
    """The brief and the project's rules: the part of a prompt that is the same every time.

    The repo map is added here once there is one.
    """
    brief = _body((Path(briefs) / f"{task.key}.md").read_bytes().decode("utf-8"))
    rules = next(
        (
            (n, (Path(repo) / n).read_bytes().decode("utf-8"))
            for n in RULES_FILES
            if (Path(repo) / n).is_file()
        ),
        None,
    )
    parts = [f"# Task {task.key}: {task.title}", f"Type: {task.brief.type}", brief]
    if task.brief.adr != "none":
        parts.append(f"Linked ADR: {task.brief.adr}")
    parts.append(
        f"## Project rules ({rules[0]})\n{rules[1].strip()}"
        if rules
        else "## Project rules\n(none found)"
    )
    return "\n\n".join(parts)


def _choice(settings: Settings, repo: Path, stage: str, skills_root: Path | None) -> MethodChoice:
    skill = settings.methods[stage].skill if stage in settings.methods else stage
    try:
        found = resolve(settings, repo, stage, skills_root=skills_root)
    except MethodRefused as exc:
        return MethodChoice(stage, skill, "refused", str(exc))
    return MethodChoice(stage, found.skill, found.source, found.reason)


def _task(repo: Path, key: str) -> tuple[Task, Path, bool, str]:
    board, briefs, _ = locations(repo)
    loaded = load_tasks(board, briefs)
    task = loaded.tasks.get(key)
    if task is None:
        reason = next((r.reason for r in loaded.rejections if r.key == key), None)
        if loaded.states.get(key) is State.DONE:
            reason = "it is DONE; there is nothing to run"
        raise CommandError(f"cannot plan {key}: {reason or 'no such work item on the board'}")
    chosen = select(loaded)
    if any(t.key == key for t in chosen.ready):
        return task, Path(briefs), True, ""
    why = chosen.waiting.get(key) or f"its state is {task.state.value}, not BACKLOG"
    return task, Path(briefs), False, why


def plan_run(repo: Path, key: str, *, skills_root: Path | None = None) -> RunPlan:
    """Work out the branch, worktree, methods and prompts a run of `key` would use."""
    repo = Path(repo)
    settings = load_config(repo)
    task, briefs, ready, why = _task(repo, key)
    kind = task.brief.type
    base = base_prompt(repo, briefs, task)
    stages = {
        "tester": tester_stage(kind),
        "coder": "implement",
        "diagnose": "diagnose",
        "reviewer": "review",
    }
    return RunPlan(
        key=key,
        title=task.title,
        type=kind,
        branch=branch_name(key, kind, task.title, settings.git),
        worktree=default_root(repo) / key,
        base=settings.git.base_branch,
        ready=ready,
        why_not=why,
        methods={
            role: _choice(settings, repo, stage, skills_root) for role, stage in stages.items()
        },
        tester_prompt=f"{base}\n\n{tester_instructions(kind)}",
        coder_prompt=f"{base}\n\n{INSTRUCTIONS}\n\n{CARRIED}",
    )


def render_plan(plan: RunPlan) -> str:
    """The dry-run output."""
    lines = [
        f"{plan.key}: {plan.title}",
        f"  type:      {plan.type}",
        f"  selection: {'ready to run' if plan.ready else 'not selectable now: ' + plan.why_not}",
        f"  branch:    {plan.branch} (cut from {plan.base})",
        f"  worktree:  {plan.worktree}",
        "  methods:",
    ]
    for role, m in plan.methods.items():
        extra = f" ({m.detail})" if m.detail else ""
        lines.append(f"    {role:<9} {m.stage} -> {m.skill} [{m.source}]{extra}")
    lines += [
        "",
        "--- Tester prompt " + "-" * 50,
        plan.tester_prompt,
        "",
        "--- Coder prompt (first iteration) " + "-" * 33,
        plan.coder_prompt,
        "",
        "Dry run: no model was called, and git and the logs were not touched.",
    ]
    return "\n".join(lines) + "\n"
