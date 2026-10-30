"""Recover from interruption and subscription limits: pause, resume, reset, handoff (FR-13, FR-9i,
FR-18b).

A usage-window error does not fail a task: `pause_task` keeps its state, writes `PAUSED` in Notes
and remembers when the window resets. The window itself is a small ledger (`.adlc/window.json`)
that `max_tasks_per_window` is checked against. `resume` hands back the carried state (worktree,
branch, progress.md, last failure); `reset` throws it away; `handoff` writes a note that links to
the artifacts instead of copying them, with secrets removed.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adlc import pr as pr_module
from adlc.board import Board
from adlc.commands import (
    PAUSE_DIR,
    CommandError,
    latest_run,
    locations,
    row_state,
    update_row,
)
from adlc.config import Settings, load_config
from adlc.escalation import STORE as ESCALATIONS
from adlc.escalation import list_open
from adlc.gitwork import list_worktrees, remove_worktree
from adlc.loop import LoopOutcome, Stop
from adlc.redact import redact
from adlc.runner import AgentResult
from adlc.states import Actor, State

WINDOW = timedelta(hours=5)  # the Claude Code usage window; used when the provider gives no time
LEDGER = Path(".adlc") / "window.json"
HANDOFF = Path(".adlc") / "handoff"
_MAX_FAILURE = 600  # characters of the last failure quoted in a handoff note


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- the window ledger --------------------------------------------------------------------------


def _ledger(repo: Path) -> dict:
    return _read(Path(repo) / LEDGER) or {"starts": [], "reset_at": None}


def _counted(ledger: dict, now: datetime) -> set[str]:
    """Tasks started in the current window."""
    reset = ledger.get("reset_at")
    reset = _aware(datetime.fromisoformat(reset)) if reset else None
    keys = set()
    for start in ledger["starts"]:
        at = _aware(datetime.fromisoformat(start["at"]))
        if at <= now - WINDOW:
            continue
        if reset is not None and reset <= now and at < reset:
            continue  # a limit was hit and has since reset: that window is over
        keys.add(start["key"])
    return keys


def can_start(repo: Path, settings: Settings, now: datetime | None = None, key: str | None = None):
    """True if another task may start in this window; a task already counted always may."""
    counted = _counted(_ledger(repo), _now(now))
    return key in counted or len(counted) < settings.limits.max_tasks_per_window


def record_start(repo: Path, key: str, now: datetime | None = None) -> None:
    now = _now(now)
    ledger = _ledger(repo)
    if key in _counted(ledger, now):
        return
    ledger["starts"].append({"key": key, "at": now.isoformat()})
    ledger["starts"] = ledger["starts"][-200:]
    _write(Path(repo) / LEDGER, ledger)


def note_limit_hit(repo: Path, resets_at: datetime) -> None:
    """The provider said the window ends at `resets_at`; starts before then stop counting."""
    ledger = _ledger(repo)
    ledger["reset_at"] = _aware(resets_at).isoformat()
    _write(Path(repo) / LEDGER, ledger)


# --- pause --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pause:
    key: str
    resets_at: datetime
    since: datetime
    detail: str


def _pause_path(repo: Path, key: str) -> Path:
    return Path(repo) / PAUSE_DIR / f"{key}.json"


def current_pause(repo: Path, key: str) -> Pause | None:
    data = _read(_pause_path(repo, key))
    if not data:
        return None
    try:
        return Pause(
            key,
            datetime.fromisoformat(data["resets_at"]),
            datetime.fromisoformat(data["since"]),
            data.get("detail", ""),
        )
    except (KeyError, ValueError):
        return None


def pause_task(
    repo: Path,
    key: str,
    *,
    resets_at: datetime | None,
    detail: str = "",
    now: datetime | None = None,
) -> Pause:
    """Mark `key` PAUSED in Notes: same state, no failure, no escalation (FR-9i)."""
    now = _now(now)
    state = row_state(repo, key)
    if state is not State.IN_PROGRESS:
        raise CommandError(f"{key} is {state.value}; only an IN_PROGRESS task can be paused")
    resets = _aware(resets_at) if resets_at else now + WINDOW
    pause = Pause(key, resets, now, detail)
    _write(
        _pause_path(repo, key),
        {"resets_at": resets.isoformat(), "since": now.isoformat(), "detail": detail},
    )
    note_limit_hit(repo, resets)
    why = f": {detail}" if detail else ""
    update_row(repo, key, None, f"PAUSED until {resets.isoformat()}{why}")
    return pause


def pause_from_outcome(
    repo: Path, key: str, outcome: LoopOutcome, now: datetime | None = None
) -> Pause | None:
    """Pause the task if the loop stopped on a usage limit; any other outcome is not ours."""
    if outcome.stop is not Stop.PAUSED:
        return None
    return pause_task(repo, key, resets_at=outcome.resets_at, detail=outcome.detail, now=now)


def pause_hook(repo: Path, key: str, now: datetime | None = None) -> Callable[[AgentResult], None]:
    """An `on_pause` callback for the loop: records the provider's reset time."""

    def hook(result: AgentResult) -> None:
        limit = result.rate_limit
        pause_task(
            repo,
            key,
            resets_at=limit.resets_at if limit else None,
            detail=result.detail,
            now=now,
        )

    return hook


def due_pauses(repo: Path, now: datetime | None = None) -> list[Pause]:
    """Paused tasks whose usage window has reset: the orchestrator may resume them."""
    now = _now(now)
    found = []
    for path in sorted((Path(repo) / PAUSE_DIR).glob("*.json")):
        pause = current_pause(repo, path.stem)
        if pause and _aware(pause.resets_at) <= now:
            found.append(pause)
    return found


# --- resume -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resume:
    key: str
    worktree: Path
    branch: str
    run_dir: Path | None
    progress: Path | None  # progress.md, if the Coder left one
    last_failure: str
    was_paused: bool


def _entry(repo: Path, key: str):
    return next((w for w in list_worktrees(repo) if w.path.name == key and not w.is_primary), None)


def _last_failure(repo: Path, key: str) -> tuple[Path | None, str]:
    run = latest_run(repo, key)
    if run is None:
        return None, ""
    record = _read(run[1] / "loop.json") or {}
    stop, detail = record.get("stop"), record.get("detail")
    text = f"{stop}: {detail}" if stop and detail else str(stop or "")
    signature = record.get("last_signature")
    if signature:
        text = f"{text} (last failing: {signature})" if text else str(signature)
    return run[1], text


def resume(repo: Path, key: str, *, now: datetime | None = None, force: bool = False) -> Resume:
    """Hand back the carried state of an interrupted task so the loop can continue from it."""
    repo, now = Path(repo), _now(now)
    state = row_state(repo, key)
    if state is not State.IN_PROGRESS:
        hint = " (use `adlc retry` for an escalated task)" if state is State.NEEDS_HUMAN else ""
        raise CommandError(f"{key} is {state.value}; only an IN_PROGRESS task can be resumed{hint}")
    entry = _entry(repo, key)
    if entry is None:
        raise CommandError(f"{key} has no worktree to continue in; `adlc reset {key}` starts over")
    pause = current_pause(repo, key)
    if pause and _aware(pause.resets_at) > now and not force:
        raise CommandError(
            f"{key} is paused until {pause.resets_at.isoformat()} (the usage window has not "
            "reset); use --force to try anyway"
        )
    settings = load_config(repo)
    if not force and not can_start(repo, settings, now, key):
        raise CommandError(
            f"max_tasks_per_window ({settings.limits.max_tasks_per_window}) is reached for this "
            "usage window; wait for it to reset or use --force"
        )
    run_dir, failure = _last_failure(repo, key)
    progress = entry.path / "progress.md"
    _pause_path(repo, key).unlink(missing_ok=True)
    note = "Resumed after a usage-window pause." if pause else "Resumed."
    update_row(repo, key, None, note)
    record_start(repo, key, now)
    return Resume(
        key,
        entry.path,
        entry.branch or "",
        run_dir,
        progress if progress.is_file() else None,
        failure,
        pause is not None,
    )


# --- reset --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResetReport:
    worktree_removed: bool
    branch_deleted: bool
    cleared: tuple[str, ...]


_STARTED = (State.IN_PROGRESS, State.IN_REVIEW, State.NEEDS_HUMAN, State.FAILED)


def reset(repo: Path, key: str, *, yes: bool = False) -> ResetReport:
    """Throw away a started task's carried state and put it back on the queue.

    Deletes the worktree and branch (uncommitted work is lost), the pause and any open
    escalation. The run logs are kept as the record of what happened.
    """
    repo = Path(repo)
    state = row_state(repo, key)
    if state not in _STARTED:
        raise CommandError(f"{key} is {state.value}; reset applies to a task that has started")
    saved = _read(repo / pr_module.STORE / f"{key}.json")
    if saved:
        raise CommandError(
            f"{key} has an open PR (#{saved.get('number')}); close it first, then reset"
        )
    entry = _entry(repo, key)
    if not yes:
        where = f"worktree {entry.path} and branch {entry.branch}" if entry else "no worktree"
        raise CommandError(
            f"reset would delete {where}, clear the pause and any escalation, and return {key} "
            "to BACKLOG; the run logs are kept. Pass --yes to do it"
        )
    removal = remove_worktree(repo, key, force=True, root=entry.path.parent if entry else None)
    cleared = []
    for path in (
        _pause_path(repo, key),
        repo / ESCALATIONS / f"{key}.json",
        repo / ESCALATIONS / f"{key}.note.md",
    ):
        if path.is_file():
            path.unlink()
            cleared.append(path.relative_to(repo).as_posix())
    update_row(repo, key, State.BACKLOG, "Reset by a person; the run logs are kept.", Actor.HUMAN)
    return ResetReport(removal.worktree_removed, removal.branch_deleted, tuple(cleared))


# --- handoff ------------------------------------------------------------------------------------


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _next_step(key: str, state: State, pause: Pause | None, escalation: dict | None) -> str:
    if pause:
        when = pause.resets_at.isoformat()
        return f"Wait for the usage window to reset ({when}), then `adlc resume {key}`."
    if escalation:
        steps = [e["next_step"] for e in escalation["escalations"] if e.get("next_step")]
        return " ".join(steps) or f"Answer it: `adlc approve|retry|edit|reject {key}`."
    if state is State.IN_PROGRESS:
        return f"`adlc resume {key}` to continue, or `adlc reset {key}` to start over."
    if state is State.FAILED:
        return f"`adlc reset {key}`, fix the cause, then run it again."
    return "Nothing to do."


def handoff(repo: Path, key: str, *, now: datetime | None = None) -> Path:
    """Write `.adlc/handoff/<KEY>.md`: a short resume note that links to artifacts (FR-18b)."""
    repo, now = Path(repo), _now(now)
    state = row_state(repo, key)
    board_path, _, _ = locations(repo)
    row = next(r for r in Board(board_path).rows() if r.key == key)
    cells = {k.lower(): v for k, v in row.cells.items()}
    entry = _entry(repo, key)
    run_dir, failure = _last_failure(repo, key)
    pause = current_pause(repo, key)
    escalation = next((r for r in list_open(repo) if r["key"] == key), None)
    saved = _read(repo / pr_module.STORE / f"{key}.json")

    lines = [
        f"# Handoff: {key} - {cells.get('title', '')}",
        "",
        f"- Written: {now.isoformat(timespec='seconds')}",
        f"- State: {state.value}",
        f"- Notes: {cells.get('notes', '') or '(none)'}",
        f"- Branch: {entry.branch if entry and entry.branch else '(none)'}",
        f"- Worktree: {entry.path if entry else '(none)'}",
    ]
    if saved:
        lines.append(f"- PR: {saved.get('url')}")
    if run_dir:
        lines += [
            f"- Run log: {_rel(repo, run_dir / 'summary.md')}",
            f"- Loop record: {_rel(repo, run_dir / 'loop.json')}",
        ]
    else:
        lines.append("- Run log: (no run log yet)")
    if failure:
        lines += ["", "## Last failure", failure[:_MAX_FAILURE]]
    if pause:
        lines += ["", "## Paused", f"Usage window resets at {pause.resets_at.isoformat()}."]
    if escalation:
        lines += ["", "## Waiting for a person"]
        lines += [f"- {e['trigger']}: {e['reason']}" for e in escalation["escalations"]]
    lines += ["", "## Suggested next step", _next_step(key, state, pause, escalation), ""]

    path = repo / HANDOFF / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact("\n".join(lines)), encoding="utf-8", newline="\n")
    return path
