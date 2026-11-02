"""Escalating, pausing, resetting or starting a task keeps the Notes text (ADLC-036)."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adlc.board import Board
from adlc.commands import update_row
from adlc.escalation import (
    Escalation,
    Trigger,
    approve,
    edit,
    raise_escalation,
    reject,
    retry,
)
from adlc.recovery import pause_task, reset, resume
from adlc.states import State
from test_commands import board_text, make_repo

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
STOP = Escalation(Trigger.LOOP_EXHAUSTED, "gave up", (), "look at it")

ORIGINALS = {
    "empty": "",
    "one line": "Needs ADLC-034 first.",
    "pipe in backticks": "Run `a|b` before this; see `x|y|z`.",
}


@pytest.fixture(params=sorted(ORIGINALS))
def original(request):
    return ORIGINALS[request.param]


def make(tmp_path: Path, status: str, original: str) -> Path:
    return make_repo(tmp_path, [("T-001", status, original)])


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def git(repo: Path, *args: str) -> None:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def mark_resumable(repo: Path) -> None:
    """Give the task a worktree, which resume needs to continue in."""
    from adlc.config import load_config
    from adlc.gitwork import create_worktree

    create_worktree(
        repo, "T-001", "feature", "t", load_config(repo).git, root=repo.parent / "wt", fetch=False
    )


def notes(repo: Path, key="T-001") -> str:
    row = next(r for r in Board(repo / "docs" / "WORK-ITEMS.md").rows() if r.key == key)
    return row.cells["Notes"]


def raw(original: str) -> str:
    return original.replace("|", "\|")


def keeps(repo: Path, original: str, new: str) -> None:
    text = notes(repo)
    assert text.startswith(raw(original)), text
    assert new in text, text
    assert "\n" not in board_text(repo).split("| T-001 |")[1].split("\n")[0]


def test_starting_a_task_keeps_the_note(tmp_path, original):
    repo = make(tmp_path, "BACKLOG", original)
    update_row(repo, "T-001", State.IN_PROGRESS, "Started on feat/x")
    keeps(repo, original, "Started on feat/x")


def test_escalating_keeps_the_note(tmp_path, original):
    repo = make(tmp_path, "IN_PROGRESS", original)
    raise_escalation(repo, "T-001", STOP)
    keeps(repo, original, "NEEDS_HUMAN: loop_exhausted: gave up")


def test_pausing_keeps_the_note(tmp_path, original):
    repo = make(tmp_path, "IN_PROGRESS", original)
    pause_task(repo, "T-001", resets_at=None, detail="limit", now=NOW)
    keeps(repo, original, "PAUSED until")


def test_resetting_keeps_the_note(tmp_path, original):
    repo = make(tmp_path, "IN_PROGRESS", original)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    reset(repo, "T-001", yes=True)
    keeps(repo, original, "Reset by a person")


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (lambda r: approve(r, "T-001"), "approved"),
        (lambda r: retry(r, "T-001", note="try again"), "Retry requested: try again"),
        (lambda r: edit(r, "T-001"), "Being edited"),
        (lambda r: reject(r, "T-001", reason="no"), "Rejected by a person: no"),
    ],
)
def test_a_persons_answer_keeps_the_original_note(tmp_path, original, answer, text):
    repo = make(tmp_path, "IN_PROGRESS", original)
    raise_escalation(repo, "T-001", STOP)
    answer(repo)
    keeps(repo, original, text)


def test_the_same_note_is_not_added_twice(tmp_path):
    repo = make(tmp_path, "IN_PROGRESS", "first")
    update_row(repo, "T-001", None, "Started on feat/x")
    update_row(repo, "T-001", None, "Started on feat/x")
    assert notes(repo).count("Started on feat/x") == 1


def test_only_the_status_and_notes_cells_change(tmp_path):
    repo = make(tmp_path, "IN_PROGRESS", "Run `a|b` first.")
    before = board_text(repo).splitlines()
    raise_escalation(repo, "T-001", STOP)
    after = board_text(repo).splitlines()
    (line,) = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    cells_before = [c.strip() for c in before[line].split(" | ")]
    cells_after = [c.strip() for c in after[line].split(" | ")]
    assert cells_before[:3] == cells_after[:3]


def test_resuming_removes_the_pause_marker_but_keeps_the_note(tmp_path, original):
    repo = make(tmp_path, "IN_PROGRESS", original)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    pause_task(repo, "T-001", resets_at=NOW, detail="usage limit reached", now=NOW)
    assert "PAUSED until" in notes(repo)
    mark_resumable(repo)
    resume(repo, "T-001", now=NOW, force=True)
    text = notes(repo)
    assert "PAUSED" not in text and "usage limit reached" not in text
    assert text.startswith(raw(original)) and "Resumed" in text
