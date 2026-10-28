"""`adlc refine`: an interactive grilling session that turns an idea or DRAFT into a ready brief.

The driver launches a Claude Code session with the pinned grilling method, lets it write only the
brief and a proposals file, then checks the result itself: nothing else changed, the brief passes
the validator, and only the person's explicit yes moves the item to BACKLOG (FR-30, decision 19).
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from adlc.board import Board
from adlc.commands import CommandError, add_item, locations
from adlc.config import load_config
from adlc.stages import MethodRefused, resolve
from adlc.states import Actor, State, StateError, check_transition, parse_state
from adlc.tasks import KEY_PATTERN, load_tasks

REFINABLE = (State.DRAFT, State.BACKLOG)
CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md", "GLOSSARY.md")
ADR_FOLDERS = ("adr", "docs/adr")

Launcher = Callable[[str, str, Path], int]


@dataclass(frozen=True)
class Outcome:
    key: str
    promoted: bool  # moved from DRAFT to BACKLOG by this run
    state: str  # the item's state afterwards
    proposals: str | None  # path of the proposals file, if the session wrote one


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask(question: str) -> bool:
    try:
        return input(question).strip().lower() == "y"
    except EOFError:
        return False


def claude_launcher(prompt: str, opening: str, repo: Path) -> int:
    """Run an interactive Claude Code session with `prompt` appended to its instructions."""
    exe = shutil.which("claude")
    if exe is None:
        raise CommandError("the claude command was not found on PATH; install Claude Code first")
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "adlc-refine.md"
        file.write_text(prompt, encoding="utf-8")
        done = subprocess.run([exe, "--append-system-prompt-file", str(file), opening], cwd=repo)
    return done.returncode


# --- the prompt -------------------------------------------------------------------------------


def _cell(row, name: str) -> str:
    return next((v for k, v in row.cells.items() if k.lower() == name), "")


def _dependencies(brief: Path) -> list[str]:
    try:
        lines = brief.read_bytes().decode("utf-8").split("\n")
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
        deps = (yaml.safe_load("\n".join(lines[1:end])) or {}).get("depends_on") or []
    except (OSError, UnicodeDecodeError, StopIteration, yaml.YAMLError, AttributeError):
        return []
    return [d for d in deps if isinstance(d, str) and KEY_PATTERN.match(d)]


def _context(repo: Path, board_rel: str, briefs_rel: str, key: str) -> list[str]:
    found = [name for name in CONTEXT_FILES if (repo / name).is_file()]
    found += [f"{name}/" for name in ADR_FOLDERS if (repo / name).is_dir()]
    found.append(board_rel)
    for dep in _dependencies(repo / briefs_rel / f"{key}.md"):
        if (repo / briefs_rel / f"{dep}.md").is_file():
            found.append(f"{briefs_rel}/{dep}.md")
    return found


def build_prompt(
    method: str, key: str, title: str, board_rel: str, briefs_rel: str, repo: Path
) -> str:
    brief, proposals = f"{briefs_rel}/{key}.md", f".adlc/proposals/{key}.md"
    reading = "\n".join(f"- {item}" for item in _context(repo, board_rel, briefs_rel, key))
    state = "exists; continue from it" if (repo / brief).is_file() else "does not exist yet"
    return f"""{method}
## Your task: refine work item {key}

Title: {title}
Brief file: {brief} ({state})

## Read first, before asking anything

{reading}

Look up facts in the repository yourself; never ask the person for something you can find.

## Rules for this session

- A person is present. Ask in numbered rounds. Give a recommended answer for every question,
  worded so that "yes" accepts it. Finish only when the person confirms the shared understanding.
- You may write exactly two files: the brief, {brief}, and a proposals file, {proposals}.
  Do not edit the board, GLOSSARY.md, any ADR, or any other file.
- If you find new terms or hard-to-reverse decisions, write them as proposals in the proposals
  file for the person to review. Never create a glossary entry or an ADR yourself.
- Do not change the item's status. The driver validates the brief afterwards and the person
  decides whether it moves to BACKLOG.

## The brief must look like this

```
---
key: {key}
type: feature            # feature, bugfix, hotfix, refactor, spike, docs or test
priority: 1              # whole number, 1 is first
depends_on: []           # keys of items that must be DONE first
adr: none                # none, needed, or ADR-0000
requires_approval: false # true when a person must approve before it merges
---
# {key}: {title}

**Description:** ...

**Acceptance criteria:**
- one observable, checkable statement per bullet
```
"""


# --- checking what the session did --------------------------------------------------------------


def _snapshot(repo: Path) -> dict[str, tuple[str, str | None]] | None:
    """Every changed or untracked path with its status and content hash; None outside git."""
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    found = {}
    for entry in filter(None, done.stdout.split("\0")):
        status, name = entry[:2], entry[3:]
        file = repo / name
        digest = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
        found[name] = (status, digest)
    return found


def _summary(task, title: str) -> str:
    brief = task.brief
    deps = ", ".join(brief.depends_on) or "nothing"
    return (
        f"{task.key}: {title}\n"
        f"  type {brief.type}, priority {brief.priority}, depends on {deps}\n"
        f"  {len(brief.criteria)} acceptance criteria, "
        f"requires approval: {'yes' if brief.requires_approval else 'no'}, ADR: {brief.adr}"
    )


def refine(
    repo: Path,
    target: str,
    *,
    code: str | None = None,
    new_code: bool = False,
    launcher: Launcher | None = None,
    confirm: Callable[[str], bool] | None = None,
    interactive: bool | None = None,
    skills_root: Path | None = None,
) -> Outcome:
    """Refine `target` (a key, or the text of a new idea) in a session with a person."""
    launcher = launcher or claude_launcher
    confirm = confirm or ask
    if not (is_interactive() if interactive is None else interactive):
        raise CommandError("adlc refine needs a person at a terminal; it cannot run unattended")
    settings = load_config(repo)
    try:
        method = resolve(settings, repo, "frame", unattended=False, skills_root=skills_root)
    except MethodRefused as exc:
        raise CommandError(str(exc)) from exc

    board_path, briefs, briefs_rel = locations(repo)
    board = Board(board_path)
    if KEY_PATTERN.match(target):
        key = target
        if key not in {row.key for row in board.rows()}:
            raise CommandError(f"no work item {key!r} on the board")
    else:
        use = code or settings.board.code
        if not use:
            raise CommandError(
                "give --code CODE (or set board.code in the config) to start an idea"
            )
        key = add_item(repo, use, target, new_code=new_code)
        print(f"added {key} as a DRAFT")
    row = next(r for r in board.rows() if r.key == key)
    try:
        state = parse_state(_cell(row, "status"))
    except StateError as exc:
        raise CommandError(str(exc)) from exc
    if state not in REFINABLE:
        raise CommandError(
            f"{key} is {state.value}; only DRAFT and BACKLOG items can be refined "
            "(a spec problem is retried with `retry --refine`)"
        )

    title = _cell(row, "title")
    brief_rel, proposals_rel = f"{briefs_rel}/{key}.md", f".adlc/proposals/{key}.md"
    board_rel = board_path.relative_to(repo).as_posix()
    prompt = build_prompt(method.text, key, title, board_rel, briefs_rel, repo)
    opening = f"Refine work item {key}: {title}. Follow the method in your instructions."

    before = _snapshot(repo)
    exit_code = launcher(prompt, opening, repo)
    after = _snapshot(repo)
    if exit_code != 0:
        raise CommandError(
            f"the session ended with an error (exit {exit_code}); the brief was not checked"
        )
    if before is None or after is None:
        print("note: not a git repository, so edits outside the brief were not checked")
    else:
        allowed = {brief_rel, proposals_rel}
        stray = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        stray = [p for p in stray if p not in allowed]
        if stray:
            raise CommandError(
                "the session changed files other than the brief and the proposals file: "
                f"{', '.join(stray)}. Nothing was reverted; review them before going on."
            )

    if not (repo / brief_rel).is_file():
        raise CommandError(f"no brief was written at {brief_rel}")
    loaded = load_tasks(board_path, briefs, include_drafts=True)
    problem = next((r.reason for r in loaded.rejections if r.key == key), None)
    if problem is not None or key not in loaded.tasks:
        raise CommandError(f"the brief for {key} is not ready: {problem}")
    proposals = proposals_rel if (repo / proposals_rel).is_file() else None
    if proposals:
        print(f"proposals for you to review (nothing was applied): {proposals}")
    print(_summary(loaded.tasks[key], title))

    if state is State.BACKLOG:
        print(f"{key} stays BACKLOG; its brief is valid")
        return Outcome(key, False, State.BACKLOG.value, proposals)
    if not confirm(f"Move {key} to BACKLOG? [y/N] "):
        print(f"{key} stays DRAFT")
        return Outcome(key, False, State.DRAFT.value, proposals)
    check_transition(State.DRAFT, State.BACKLOG, Actor.HUMAN)
    board.set_cell(key, "Status", State.BACKLOG.value, expected=_cell(row, "status"))
    print(f"{key} is now BACKLOG")
    return Outcome(key, True, State.BACKLOG.value, proposals)
