"""resume, reset and handoff through the command line (ADLC-022)."""

import pytest

from adlc.cli import main
from adlc.recovery import current_pause
from test_pr import make_scenario


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@pytest.fixture
def sc(tmp_path):
    return make_scenario(tmp_path)


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_resume_prints_where_to_continue(sc, capsys):
    code, out, _ = run(capsys, "resume", "T-001", "--repo", str(sc.repo))
    assert code == 0
    assert "T-001" in out
    assert "feature/" in out


def test_resume_of_a_task_that_is_not_in_progress_exits_1(tmp_path, capsys):
    sc = make_scenario(tmp_path)
    run(capsys, "reset", "T-001", "--yes", "--repo", str(sc.repo))
    code, _, err = run(capsys, "resume", "T-001", "--repo", str(sc.repo))
    assert code == 1
    assert "BACKLOG" in err


def test_reset_without_yes_changes_nothing(sc, capsys):
    code, _, err = run(capsys, "reset", "T-001", "--repo", str(sc.repo))
    assert code == 1
    assert "--yes" in err
    assert sc.worktree.exists()


def test_reset_with_yes(sc, capsys):
    code, out, _ = run(capsys, "reset", "T-001", "--yes", "--repo", str(sc.repo))
    assert code == 0
    assert not sc.worktree.exists()
    assert sc.state() == "BACKLOG"
    assert current_pause(sc.repo, "T-001") is None
    assert "T-001" in out


def test_handoff_prints_the_path(sc, capsys):
    code, out, _ = run(capsys, "handoff", "T-001", "--repo", str(sc.repo))
    assert code == 0
    assert "T-001.md" in out
    assert (sc.repo / ".adlc" / "handoff" / "T-001.md").is_file()
