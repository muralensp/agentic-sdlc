"""What `adlc add`, `adlc status` and `adlc log` do (PRD FR-5, FR-19, FR-27).

The functions take a repository folder and return data or text; `adlc.cli` handles arguments, output
and exit codes. Reading never writes. `add_item` is the only writer and works under the board lock.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from adlc.board import Board, Row
from adlc.config import load_config
from adlc.states import Actor, State, check_transition, parse_state
from adlc.tasks import (
    CODE_PATTERN,
    KEY_PATTERN,
    TYPES,
    _order,
    allocate_key,
    load_tasks,
    next_priority,
    select,
)

STOP: Path | None = None  # find_repo gives up at this folder; set by tests so they stay isolated
_RUN = re.compile(r"^run-(\d+)$")
_GROUPS = (
    ("attention", "NEEDS ATTENTION"),
    ("in_flight", "IN FLIGHT"),
    ("ready", "READY"),
    ("waiting", "WAITING"),
    ("draft", "DRAFT"),
    ("problem", "BRIEF PROBLEMS"),
)
_GROUP_OF = {
    State.FAILED: "attention",
    State.NEEDS_HUMAN: "attention",
    State.BLOCKED: "attention",
    State.IN_PROGRESS: "in_flight",
    State.IN_REVIEW: "in_flight",
    State.DRAFT: "draft",
    State.DONE: "done",
}
_NOTE_WIDTH = 100
STUCK_FACTOR = 2  # an IN_PROGRESS task idle for this many times max_task_minutes is flagged (FR-13)
PAUSE_DIR = Path(".adlc") / "pauses"  # a paused task is waiting, not stuck (ADLC-022)


class CommandError(Exception):
    """The request cannot be carried out (a mistake by the person using the command)."""


def find_repo(start: Path, stop: Path | None = None) -> Path:
    """The nearest folder at or above `start` that holds `.adlc` or `.git`."""
    stop = stop or STOP
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / ".adlc").is_dir() or (folder / ".git").exists():
            return folder
        if folder == stop:
            break
    raise CommandError(f"{start} is not inside a repository (no .adlc or .git folder above it)")


def _cell(row: Row, name: str) -> str:
    return next((v for k, v in row.cells.items() if k.lower() == name), "").replace("\\|", "|")


def locations(repo: Path) -> tuple[Path, Path, str]:
    settings = load_config(repo)
    briefs = settings.board.briefs.strip("/")
    return repo / settings.board.path, repo / briefs, briefs


def row_state(repo: Path, key: str) -> State:
    """The board's state for `key`."""
    board_path, _, _ = locations(repo)
    for row in Board(board_path).rows():
        if row.key == key:
            return parse_state(_cell(row, "status"))
    raise CommandError(f"{key} is not on the board")


def update_row(
    repo: Path, key: str, status: State | None, notes: str, actor: Actor = Actor.ORCHESTRATOR
) -> None:
    """Set a row's Status (if given, and legal for `actor`) and Notes under the board lock."""
    board_path, _, _ = locations(repo)
    board = Board(board_path)
    current = row_state(repo, key)
    change = status is not None and status is not current
    if change:
        check_transition(current, status, actor)
    with board.lock():
        if change:
            board.set_cell(key, "Status", status.value)
        board.set_cell(key, "Notes", " ".join(notes.split()))


# --- add --------------------------------------------------------------------------------------


def _skeleton(key: str, title: str, kind: str, priority: int, depends: list[str]) -> str:
    return (
        f"---\nkey: {key}\ntype: {kind}\npriority: {priority}\n"
        f"depends_on: [{', '.join(depends)}]\nadr: none\nrequires_approval: false\n---\n"
        f"# {key}: {title}\n\n**Description:** TBD\n\n**Acceptance criteria:**\n- TBD\n"
    )


def add_item(
    repo: Path,
    code: str,
    title: str,
    *,
    kind: str = "feature",
    priority: int | None = None,
    depends: list[str] | tuple[str, ...] = (),
    prd: str = "",
    new_code: bool = False,
) -> str:
    """Add a DRAFT row and a brief skeleton under the next key for `code`; return the key."""
    title = title.strip()
    if not CODE_PATTERN.match(code):
        raise CommandError(
            f"invalid code {code!r}: capital letters and digits, starting with a letter"
        )
    if not title or "\n" in title or "\r" in title:
        raise CommandError("the title must be one line of text")
    if kind not in TYPES:
        raise CommandError(f"type {kind!r} must be one of {', '.join(TYPES)}")
    if priority is not None and priority < 1:
        raise CommandError("priority must be a whole number of 1 or more")
    depends = list(depends)
    bad = [d for d in depends if not KEY_PATTERN.match(d)]
    if bad:
        raise CommandError(f"depends: {', '.join(bad)} is not of the form CODE-123")

    board_path, briefs, briefs_rel = locations(repo)
    board = Board(board_path)
    with board.lock():
        rows = board.rows()
        missing = [d for d in depends if d not in {r.key for r in rows}]
        if missing:
            raise CommandError(f"depends: {', '.join(missing)} is not on the board")
        same = [r for r in rows if r.key.rsplit("-", 1)[0] == code]
        if not same and not new_code:
            raise CommandError(
                f"no work item uses code {code!r}; if it is a new code, add --new-code"
            )
        context = _cell(same[-1], "context") if same else code
        key = allocate_key(code, board_path, briefs)
        if priority is None:
            priority = next_priority(briefs)
        briefs.mkdir(parents=True, exist_ok=True)
        brief = briefs / f"{key}.md"
        try:
            with open(brief, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(_skeleton(key, title, kind, priority, depends))
        except FileExistsError:
            raise CommandError(f"{brief} already exists; not overwriting it") from None
        values = {
            "key": key,
            "context": context,
            "title": title,
            "status": State.DRAFT.value,
            "notes": f"Brief: {briefs_rel}/{key}.md",
        }
        if prd:
            values["linked prd"] = prd
        try:
            board.add_row(values)
        except BaseException:
            brief.unlink(missing_ok=True)
            raise
    return key


# --- status -----------------------------------------------------------------------------------


def _idle_minutes(repo: Path, key: str, now: datetime) -> float | None:
    """Minutes since anything in the newest run folder changed; None if unknown or paused."""
    run = latest_run(repo, key)
    if run is None or (repo / PAUSE_DIR / f"{key}.json").is_file():
        return None
    newest = max(p.stat().st_mtime for p in [run[1], *run[1].rglob("*")])
    return (now.timestamp() - newest) / 60


def status_report(repo: Path, now: datetime | None = None) -> dict:
    """Every board item with its state, group and reason, plus the items with problems."""
    now = now or datetime.now(UTC)
    max_idle = STUCK_FACTOR * load_config(repo).limits.max_task_minutes
    board_path, briefs, _ = locations(repo)
    rows = Board(board_path).rows()
    loaded = load_tasks(board_path, briefs)
    chosen = select(loaded)
    bad = {r.key: r.reason for r in loaded.rejections}
    ready = {t.key for t in chosen.ready}
    held = {
        **chosen.waiting,
        **{k: f"dependency FAILED: {', '.join(v)}" for k, v in chosen.blocked.items()},
    }

    items: dict[str, dict] = {}
    for row in rows:
        state = loaded.states.get(row.key)
        task = loaded.tasks.get(row.key)
        if state is None:
            group, reason = "problem", bad.get(row.key, "unreadable status")
        elif state is State.BACKLOG:
            if row.key in ready:
                group, reason = "ready", ""
            elif row.key in held:
                group, reason = "waiting", held[row.key]
            else:
                group, reason = "problem", bad.get(row.key, "not selectable")
        else:
            group, reason = _GROUP_OF[state], _cell(row, "notes")
            idle = _idle_minutes(repo, row.key, now) if state is State.IN_PROGRESS else None
            if idle is not None and idle > max_idle:
                group = "attention"
                reason = (
                    f"no activity for {int(idle)} min (limit {max_idle}); "
                    f"try `adlc resume {row.key}` or `adlc reset {row.key}`"
                )
        items[row.key] = {
            "key": row.key,
            "title": _cell(row, "title"),
            "state": state.value if state else None,
            "priority": task.brief.priority if task else None,
            "depends_on": list(task.brief.depends_on) if task else [],
            "group": group,
            "reason": reason,
            "ready": row.key in ready,
        }

    order = [t.key for t in sorted(loaded.tasks.values(), key=_order)]
    by_row = {k: i for i, k in enumerate(items)}

    def position(item: dict) -> tuple[int, int]:
        names = [g for g, _ in _GROUPS] + ["done"]
        if item["group"] in ("ready", "waiting"):
            return names.index(item["group"]), order.index(item["key"])
        return names.index(item["group"]), by_row[item["key"]]

    return {
        "items": sorted(items.values(), key=position),
        "rejections": [{"key": r.key, "reason": r.reason} for r in loaded.rejections],
        "done_count": sum(1 for i in items.values() if i["state"] == State.DONE.value),
    }


def latest_run(repo: Path, key: str) -> tuple[int, Path] | None:
    folder = repo / ".adlc" / "logs" / key
    runs = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = _RUN.match(path.name)
            if match and path.is_dir():
                runs.append((int(match.group(1)), path))
    return max(runs, default=None)


def _short(text: str) -> str:
    return text if len(text) <= _NOTE_WIDTH else text[: _NOTE_WIDTH - 3] + "..."


def render_status(report: dict, *, repo: Path, all_items: bool) -> str:
    """The plain-text board: problems first, empty groups left out."""
    out: list[str] = []
    items = report["items"]
    for group, title in (*_GROUPS, ("done", "DONE")):
        members = [i for i in items if i["group"] == group]
        if group == "done":
            if not members:
                continue
            if not all_items:
                out.append(f"DONE: {report['done_count']} item(s) (use --all to list them)")
                continue
        out.append(f"{title} ({len(members)})" if members else "")
        if not members:
            out.pop()
            continue
        for item in members:
            state = item["state"] or "?"
            lead = f"  {item['key']:<10} "
            if group == "ready":
                lead += f"priority {item['priority']:<4} "
            elif group != "waiting" and group != "problem":
                lead += f"{state:<12} "
            out.append(f"{lead}{item['title']}")
            if item["reason"] and group != "done":
                out.append(f"             {_short(item['reason'])}")
            if group in ("attention", "in_flight"):
                run = latest_run(repo, item["key"])
                where = (run[1] / "summary.md").relative_to(repo).as_posix() if run else None
                out.append(f"             log: {where or 'no log yet'}")
        out.append("")
    if report["rejections"] and not any(i["group"] == "problem" for i in items):
        out.append("BRIEF PROBLEMS")
        out += [f"  {r['key']}: {r['reason']}" for r in report["rejections"]]
    return "\n".join(out).rstrip() + "\n"


# --- log --------------------------------------------------------------------------------------


def read_log(repo: Path, key: str, run: int | None = None) -> str:
    """The summary of the newest run of `key` (or of run number `run`)."""
    board_path, _, _ = locations(repo)
    if key not in {r.key for r in Board(board_path).rows()}:
        raise CommandError(f"no work item {key!r} on the board")
    if run is None:
        latest = latest_run(repo, key)
        if latest is None:
            return f"no runs yet for {key}"
        run, folder = latest
    else:
        folder = repo / ".adlc" / "logs" / key / f"run-{run}"
        if not folder.is_dir():
            raise CommandError(f"no run {run} for {key}")
    summary = folder / "summary.md"
    if not summary.is_file():
        raise CommandError(f"run {run} of {key} has no summary.md ({folder})")
    return summary.read_bytes().decode("utf-8")
