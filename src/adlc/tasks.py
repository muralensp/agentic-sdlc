"""Task briefs, loading the board with its briefs, selecting what can run, and allocating keys.

A board row alone has no acceptance criteria or dependencies; each item has a brief,
`docs/work-items/{KEY}.md`, with YAML front-matter (PRD FR-2). An item with a missing or malformed
brief is rejected with a reason and is never selectable. Nothing is guessed or defaulted.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from adlc.board import Board
from adlc.states import State, StateError, parse_state

KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9]*)-(\d{3,})$")
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*$")
TYPES = ("feature", "bugfix", "hotfix", "refactor", "spike", "docs", "test")
REQUIRED_FIELDS = ("key", "type", "priority", "depends_on", "adr", "requires_approval")
_ADR = re.compile(r"^(none|needed|ADR-\d{4})$")
_CRITERIA_HEADING = re.compile(r"^\s*(#+\s*)?(\*\*)?acceptance criteria:?(\*\*)?:?\s*$", re.I)
_PLACEHOLDERS = {"tbd", "todo", "...", "…", "n/a", "tba"}


class BriefError(ValueError):
    """A brief is missing, malformed or incomplete."""


@dataclass(frozen=True)
class Brief:
    key: str
    type: str
    priority: int
    depends_on: tuple[str, ...]
    adr: str
    requires_approval: bool
    criteria: tuple[str, ...]
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    key: str
    title: str
    state: State
    line: int  # 1-based line of the row in the board file
    brief: Brief


@dataclass(frozen=True)
class Rejection:
    key: str
    reason: str


@dataclass
class Loaded:
    tasks: dict[str, Task]  # validated items that are not DONE
    states: dict[str, State]  # every row with a readable status, DONE included
    rejections: list[Rejection]


@dataclass
class Selection:
    ready: list[Task]  # selectable now, best first
    waiting: dict[str, str]  # BACKLOG items held back, with the reason
    blocked: dict[str, list[str]]  # BACKLOG items with a FAILED dependency (should become BLOCKED)
    unblocked: list[str]  # BLOCKED items whose dependencies are all DONE (can return to BACKLOG)


def _fail(path: object, reason: str) -> BriefError:
    return BriefError(f"{path}: {reason}")


def parse_brief(text: str, path: object) -> Brief:
    """Validate a brief's front-matter and acceptance criteria."""
    lines = text.removeprefix("﻿").split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise _fail(path, "front-matter missing (the file must start with a --- line)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        raise _fail(path, "front-matter is not closed by a --- line") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {min(mark.line + 2, end)}" if mark else ""
        raise _fail(path, f"front-matter is not valid YAML{where}") from exc
    if not isinstance(data, dict):
        raise _fail(path, "front-matter must be a list of key: value fields")
    for name in REQUIRED_FIELDS:
        if name not in data:
            raise _fail(path, f"missing front-matter field '{name}'")

    key = data["key"]
    if not (isinstance(key, str) and KEY_PATTERN.match(key)):
        raise _fail(path, f"key {key!r} is not of the form CODE-123")
    if data["type"] not in TYPES:
        raise _fail(path, f"type {data['type']!r} must be one of {', '.join(TYPES)}")
    priority = data["priority"]
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        raise _fail(path, f"priority {priority!r} must be a whole number of 1 or more")
    deps = data["depends_on"]
    if not isinstance(deps, list) or not all(
        isinstance(d, str) and KEY_PATTERN.match(d) for d in deps
    ):
        raise _fail(path, "depends_on must be a list of keys such as [CODE-001]")
    if key in deps:
        raise _fail(path, "depends_on lists the item itself")
    if len(set(deps)) != len(deps):
        raise _fail(path, "depends_on has a duplicate key")
    if not (isinstance(data["adr"], str) and _ADR.match(data["adr"])):
        raise _fail(path, f"adr {data['adr']!r} must be none, needed or ADR-0000")
    if not isinstance(data["requires_approval"], bool):
        raise _fail(path, "requires_approval must be true or false")

    criteria = _criteria(lines[end + 1 :])
    if not criteria:
        raise _fail(path, "no acceptance criteria (needs at least one real bullet under it)")
    extra = {k: v for k, v in data.items() if k not in REQUIRED_FIELDS}
    return Brief(
        key, data["type"], priority, tuple(deps), data["adr"], data["requires_approval"],
        criteria, extra,
    )  # fmt: skip


def _criteria(body: list[str]) -> tuple[str, ...]:
    start = next((i for i, line in enumerate(body) if _CRITERIA_HEADING.match(line)), None)
    if start is None:
        return ()
    found = []
    for line in body[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(("- ", "* ")):
            break
        text = stripped[2:].strip()
        if text and text.lower().rstrip(".:") not in _PLACEHOLDERS and text not in _PLACEHOLDERS:
            found.append(text)
    return tuple(found)


def _cell(cells: dict[str, str], name: str) -> str:
    return next((v for k, v in cells.items() if k.lower() == name), "")


def load_tasks(board: Path, briefs: Path, include_drafts: bool = False) -> Loaded:
    """Join every board row with its brief. Bad items are rejected; the rest still load.

    DRAFT items are ideas and skip the brief checks unless `include_drafts` asks for them, as
    when a person is about to promote one to BACKLOG.
    """
    rows = Board(board).rows()
    known = {row.key for row in rows}
    loaded = Loaded({}, {}, [])
    for row in rows:
        if not KEY_PATTERN.match(row.key):
            loaded.rejections.append(
                Rejection(row.key, f"key {row.key!r} is not of the form CODE-123")
            )
            continue
        try:
            state = parse_state(_cell(row.cells, "status"))
        except StateError as exc:
            loaded.rejections.append(Rejection(row.key, str(exc)))
            continue
        loaded.states[row.key] = state
        if state is State.DONE:
            continue  # finished work needs no brief (older boards have none)
        if state is State.DRAFT and not include_drafts:
            continue  # an idea: its brief is checked when it is promoted to BACKLOG
        path = Path(briefs) / f"{row.key}.md"
        if not path.is_file():
            loaded.rejections.append(Rejection(row.key, f"no brief at {path}"))
            continue
        try:
            brief = parse_brief(path.read_bytes().decode("utf-8"), path)
        except (BriefError, UnicodeDecodeError) as exc:
            loaded.rejections.append(Rejection(row.key, str(exc)))
            continue
        if brief.key != row.key:
            loaded.rejections.append(
                Rejection(
                    row.key, f"{path}: brief key {brief.key} does not match row key {row.key}"
                )
            )
            continue
        loaded.tasks[row.key] = Task(row.key, _cell(row.cells, "title"), state, row.line, brief)

    for task in list(loaded.tasks.values()):
        unknown = [d for d in task.brief.depends_on if d not in known]
        if unknown:
            loaded.rejections.append(
                Rejection(task.key, f"depends on unknown key {', '.join(unknown)}")
            )
            del loaded.tasks[task.key]
    for key, path in _cycles(loaded.tasks).items():
        loaded.rejections.append(Rejection(key, f"dependency cycle: {' -> '.join(path)}"))
        del loaded.tasks[key]
    return loaded


def _cycles(tasks: dict[str, Task]) -> dict[str, list[str]]:
    """Every task that can reach itself through depends_on, with one path back to itself."""
    found: dict[str, list[str]] = {}
    for start in tasks:
        stack = [(start, [start])]
        seen: set[str] = set()
        while stack and start not in found:
            node, path = stack.pop()
            for dep in tasks[node].brief.depends_on if node in tasks else ():
                if dep == start:
                    found[start] = [*path, start]
                    break
                if dep in tasks and dep not in seen:
                    seen.add(dep)
                    stack.append((dep, [*path, dep]))
    return found


def _order(task: Task) -> tuple[int, str, int]:
    code, seq = task.key.rsplit("-", 1)
    return (task.brief.priority, code, int(seq))


def select(loaded: Loaded) -> Selection:
    """What can start now: BACKLOG, valid brief, every dependency DONE; best priority first."""
    chosen = Selection([], {}, {}, [])
    for task in sorted(loaded.tasks.values(), key=_order):
        deps = task.brief.depends_on
        states = {d: loaded.states.get(d) for d in deps}
        if task.state is State.BLOCKED and all(s is State.DONE for s in states.values()):
            chosen.unblocked.append(task.key)
        if task.state is not State.BACKLOG:
            continue
        if all(s is State.DONE for s in states.values()):
            chosen.ready.append(task)
        elif failed := [d for d, s in states.items() if s is State.FAILED]:
            chosen.blocked[task.key] = failed
        else:
            held = ", ".join(
                f"{d} ({s.value if s else 'rejected'})"
                for d, s in states.items()
                if s is not State.DONE
            )
            chosen.waiting[task.key] = f"waiting on {held}"
    return chosen


def next_priority(briefs: Path) -> int:
    """One above the highest priority in any brief, so a new item goes to the back of the queue.

    Reads leniently: a brief that is unfinished or malformed is skipped, not an error.
    """
    highest = 0
    if Path(briefs).is_dir():
        for path in Path(briefs).glob("*.md"):
            try:
                lines = path.read_bytes().decode("utf-8").split("\n")
                end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
                value = (yaml.safe_load("\n".join(lines[1:end])) or {}).get("priority")
            except (OSError, UnicodeDecodeError, StopIteration, yaml.YAMLError, AttributeError):
                continue
            if (
                lines[0].rstrip() == "---"
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                highest = max(highest, value)
    return highest + 1


def allocate_key(code: str, board: Path, briefs: Path) -> str:
    """The next unused key for `code`: one above the highest seen in rows or brief files."""
    if not CODE_PATTERN.match(code):
        raise ValueError(
            f"invalid code {code!r}: capital letters and digits, starting with a letter"
        )
    seen = [0]
    for row in Board(board).rows():
        match = KEY_PATTERN.match(row.key)
        if match and match.group(1) == code:
            seen.append(int(match.group(2)))
    if Path(briefs).is_dir():
        for path in Path(briefs).glob(f"{code}-*.md"):
            match = KEY_PATTERN.match(path.stem)
            if match and match.group(1) == code:
                seen.append(int(match.group(2)))
    return f"{code}-{max(seen) + 1:03d}"
