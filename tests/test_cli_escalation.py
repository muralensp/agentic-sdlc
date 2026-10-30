"""approve, retry, edit, reject and report through the command line (ADLC-020)."""

from pathlib import Path

import pytest

from adlc.cli import main
from adlc.escalation import Escalation, Trigger, merge_allowed, raise_escalation
from test_commands import board_text, make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path, [("T-001", "IN_PROGRESS"), ("T-002", "IN_PROGRESS")])
    raise_escalation(repo, "T-001", Escalation(Trigger.TASK_FLAG, "needs sign-off"))
    return repo


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_approve(repo, capsys):
    code, out, _ = run(capsys, "approve", "T-001", "--repo", str(repo))
    assert code == 0
    assert "T-001" in out
    assert merge_allowed(repo, "T-001")


def test_retry_with_a_note(repo, capsys):
    code, _, _ = run(capsys, "retry", "T-001", "--note", "try again", "--repo", str(repo))
    assert code == 0
    assert "| BACKLOG |" in board_text(repo)
    assert "try again" in (repo / ".adlc" / "escalations" / "T-001.note.md").read_text()


def test_edit_names_the_brief_to_change(repo, capsys):
    code, out, _ = run(capsys, "edit", "T-001", "--repo", str(repo))
    assert code == 0
    assert "T-001.md" in out


def test_reject_with_a_reason(repo, capsys):
    code, _, _ = run(capsys, "reject", "T-001", "--reason", "no good", "--repo", str(repo))
    assert code == 0
    assert "| FAILED |" in board_text(repo)


def test_answering_a_task_that_is_not_waiting_exits_1(repo, capsys):
    code, _, err = run(capsys, "approve", "T-002", "--repo", str(repo))
    assert code == 1
    assert "adlc:" in err
    assert "not waiting" in err


def test_report_prints_the_rates(repo, capsys):
    run(capsys, "approve", "T-001", "--repo", str(repo))
    code, out, _ = run(capsys, "report", "--repo", str(repo))
    assert code == 0
    assert "task_flag" in out


def test_status_lists_the_waiting_task_with_its_reason(repo, capsys):
    code, out, _ = run(capsys, "status", "--repo", str(repo))
    assert code == 0
    assert out.index("T-001") < out.index("T-002")
    assert "needs sign-off" in out
