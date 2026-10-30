"""Spike tasks: `research` and `prototype` (FR-35).

A brief of type `spike` names its kind in a `spike:` front-matter field and may name a `topic:`.
`research` writes one sourced note, `docs/research/<topic>.md`, and no code; the note is committed
on its `spike/` branch for the PR step to merge. `prototype` is throwaway: the agent works on a
`spike/` branch, the answer it gives is recorded under `.adlc/spikes/`, and the branch is deleted
without ever being merged. The orchestrator checks what the agent changed and moves the board; the
agent never edits the board.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from adlc.commands import CommandError, locations, row_state, update_row
from adlc.config import Settings
from adlc.gitwork import commit, create_worktree, remove_worktree, slugify
from adlc.redact import redact
from adlc.runner import AgentRequest, AgentRunner, Role
from adlc.scope import out_of_scope
from adlc.stages import MethodRefused, resolve
from adlc.states import State
from adlc.tasks import Brief, load_tasks
from adlc.tdd import changed_paths

RESEARCH, PROTOTYPE = "research", "prototype"
KINDS = (RESEARCH, PROTOTYPE)
STAGES = {RESEARCH: "learn", PROTOTYPE: "try"}  # the method stage each kind uses (PRD 7.3)
NOTE_DIR = "docs/research"
SPIKES = Path(".adlc") / "spikes"
_TOPIC = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SOURCES = re.compile(r"^#{1,6}[ \t]*sources[ \t]*$", re.IGNORECASE | re.MULTILINE)
_LINK = re.compile(r"https?://\S+")
_NOTE_LINE = 120


class SpikeError(ValueError):
    """The brief is not a usable spike."""


@dataclass(frozen=True)
class SpikeResult:
    status: str  # ready (research), done (prototype), rejected or agent_failed
    kind: str
    branch: str
    problems: tuple[str, ...] = ()
    detail: str = ""
    record: Path | None = None  # where a prototype's answer was kept


# --- the brief (pure) -------------------------------------------------------------------------


def kind_of(brief: Brief) -> str:
    """`research` or `prototype`, from a spike brief's `spike:` field."""
    if brief.type != "spike":
        raise SpikeError(f"{brief.key} is a {brief.type} task, not a spike")
    kind = brief.extra.get("spike")
    if kind not in KINDS:
        raise SpikeError(f"{brief.key}: `spike:` must be research or prototype, not {kind!r}")
    return kind


def topic_of(brief: Brief, title: str) -> str:
    """The note and branch name: the brief's `topic:`, else a slug of the title."""
    topic = brief.extra.get("topic") or slugify(title)
    if not (isinstance(topic, str) and _TOPIC.match(topic)):
        raise SpikeError(
            f"{brief.key}: topic {topic!r} must be lower case words joined by - (it names a file)"
        )
    return topic


def check_research(changes: dict[str, str], topic: str, note: str, settings: Settings) -> list[str]:
    """Why a research run is not acceptable; empty when it is: one sourced note, no code."""
    mine = f"{NOTE_DIR}/{topic}.md"
    problems = []
    stray = out_of_scope(list(changes), settings.protected, allowed=[mine])
    stray += [p for p in changes if p not in stray and p != mine]
    if stray:
        problems.append(f"research may change only {mine}; it also changed {', '.join(stray)}")
    if mine not in changes:
        problems.append(f"the note {mine} was not written")
    elif not _SOURCES.search(note):
        problems.append("the note has no Sources heading")
    elif not _LINK.search(note[_SOURCES.search(note).end() :]):
        problems.append("the Sources section has no link to a source")
    return problems


# --- running -----------------------------------------------------------------------------------

_RESEARCH_JOB = (
    "This is a research task. Use primary sources only. Write your findings to {note} and change "
    "nothing else: no code, no tests, no other files. The note must end with a `## Sources` "
    "section that links every source you used."
)
_PROTOTYPE_JOB = (
    "This is a prototype task. Build the smallest throwaway code that answers the question in the "
    "brief. Nothing you write is kept: the branch is deleted. Finish with a short written answer "
    "to the question as your final message; that answer is what is recorded."
)


def _prompt(kind: str, topic: str, brief_text: str) -> str:
    job = (
        _RESEARCH_JOB.format(note=f"{NOTE_DIR}/{topic}.md") if kind == RESEARCH else _PROTOTYPE_JOB
    )
    return f"{job}\n\n## Brief\n\n{brief_text}"


def run_spike(
    repo: Path,
    key: str,
    settings: Settings,
    runner: AgentRunner,
    *,
    root: Path | None = None,
    fetch: bool = True,
    skills_root: Path | None = None,
) -> SpikeResult:
    """Run an IN_PROGRESS spike task through its agent and finish it as its kind requires."""
    repo = Path(repo)
    state = row_state(repo, key)
    if state is not State.IN_PROGRESS:
        raise CommandError(f"{key} is {state.value}; only an IN_PROGRESS spike can run")
    board, briefs, _ = locations(repo)
    task = load_tasks(board, briefs).tasks.get(key)
    if task is None:
        raise CommandError(f"{key} has no valid brief")
    try:
        kind, topic = kind_of(task.brief), topic_of(task.brief, task.title)
    except SpikeError as exc:
        raise CommandError(str(exc)) from exc
    if kind == PROTOTYPE and (repo / ".adlc" / "prs" / f"{key}.json").exists():
        raise CommandError(f"{key} has a pull request; a prototype is never merged, so not run")
    try:
        method = resolve(settings, repo, STAGES[kind], skills_root=skills_root)
    except MethodRefused as exc:
        raise CommandError(str(exc)) from exc

    made = create_worktree(
        repo, key, "spike", task.title, settings.git, slug=topic, root=root, fetch=fetch
    )
    text = (Path(briefs) / f"{key}.md").read_text(encoding="utf-8")
    result = runner.run(
        AgentRequest(Role.CODER, _prompt(kind, topic, text), made.path, method=method.text or None)
    )
    if result.status != "ok":
        return SpikeResult("agent_failed", kind, made.branch, detail=result.detail or result.status)
    if kind == RESEARCH:
        return _finish_research(repo, key, topic, settings, made)
    return _finish_prototype(repo, key, task.title, topic, result.text, settings, made)


def _finish_research(repo, key, topic, settings, made) -> SpikeResult:
    changes = changed_paths(made.path, made.base)
    note_file = made.path / NOTE_DIR / f"{topic}.md"
    note = note_file.read_text(encoding="utf-8") if note_file.is_file() else ""
    problems = check_research(changes, topic, note, settings)
    if problems:
        return SpikeResult("rejected", RESEARCH, made.branch, tuple(problems))
    commit(made.path, f"docs(research): {topic} ({key})", settings.git)
    update_row(repo, key, State.IN_REVIEW, f"Research note {NOTE_DIR}/{topic}.md on {made.branch}")
    return SpikeResult("ready", RESEARCH, made.branch)


def _finish_prototype(repo, key, title, topic, answer, settings, made) -> SpikeResult:
    answer = redact(answer).strip()
    if not answer:
        return SpikeResult(
            "rejected", PROTOTYPE, made.branch, ("the agent gave no written answer",)
        )
    record = repo / SPIKES / f"{key}.md"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(f"# {key}: {title}\n\nPrototype `{topic}`. Answer:\n\n{answer}\n", "utf-8")
    remove_worktree(repo, key, force=True, root=made.path.parent)
    first = answer.splitlines()[0][:_NOTE_LINE]
    update_row(repo, key, State.IN_REVIEW, f"Prototype answered: {first}")
    update_row(repo, key, State.DONE, f"Prototype answered: {first} (full answer in {record.name})")
    return SpikeResult("done", PROTOTYPE, made.branch, record=record)
