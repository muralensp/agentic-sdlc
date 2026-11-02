"""`adlc finish`: close a task whose PR a person merged on the host (ADLC-039)."""

import json

import pytest

from adlc.cli import main
from adlc.commands import CommandError
from adlc.escalation import list_open
from adlc.fake_forge import FakeForge
from adlc.fake_runner import FakeRunner, ok
from adlc.pr import PASS, Check, deliver, finish
from adlc.trust import hand_merges
from test_pr import GOOD_BODY, approved_report, make_scenario, ticking


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def opened(tmp_path, state="MERGED"):
    """A task whose PR is open and waiting for a person (auto_merge off), on a forge in `state`."""
    sc = make_scenario(tmp_path, merge={"auto_merge": False})
    forge = FakeForge([[Check("ci", PASS)]], pr_state=state)
    outcome = deliver(
        sc.repo,
        "T-001",
        sc.worktree,
        runner=FakeRunner([ok(GOOD_BODY)]),
        forge=forge,
        report=approved_report(),
        dod_green=True,
        model="m",
        run_log=".adlc/logs/T-001/run-1",
        sleep=lambda s: None,
        clock=ticking(),
    )
    assert outcome.status == "escalated"
    return sc, forge


def test_a_merged_pr_closes_the_task(tmp_path):
    sc, forge = opened(tmp_path)
    finish(sc.repo, "T-001", forge=forge)
    assert sc.state() == "DONE"
    notes = sc.notes()
    assert "Merged PR #7" in notes and "pull/7" in notes
    assert "NEEDS_HUMAN" in notes  # the earlier notes are kept


def test_finishing_cleans_up_what_the_task_left(tmp_path):
    sc, forge = opened(tmp_path)
    finish(sc.repo, "T-001", forge=forge)
    assert not sc.worktree.exists()
    assert list_open(sc.repo) == []
    assert not (sc.repo / ".adlc" / "prs" / "T-001.json").exists()
    assert any(c[0] == "delete_remote_branch" for c in forge.calls)


def test_a_merge_a_person_made_counts_towards_trust(tmp_path):
    sc, forge = opened(tmp_path)
    finish(sc.repo, "T-001", forge=forge)
    assert hand_merges(sc.repo) == ["T-001"]


def test_the_escalation_is_recorded_as_approved(tmp_path):
    sc, forge = opened(tmp_path)
    finish(sc.repo, "T-001", forge=forge)
    log = (sc.repo / ".adlc" / "escalations" / "log.jsonl").read_text().splitlines()
    assert [json.loads(line)["event"] for line in log] == ["raised", "approve"]


def test_an_open_pr_is_refused_and_nothing_changes(tmp_path):
    sc, forge = opened(tmp_path, state="OPEN")
    with pytest.raises(CommandError, match="still open"):
        finish(sc.repo, "T-001", forge=forge)
    assert sc.state() == "NEEDS_HUMAN" and len(list_open(sc.repo)) == 1
    assert sc.worktree.exists() and hand_merges(sc.repo) == []


def test_a_pr_closed_without_a_merge_is_refused(tmp_path):
    sc, forge = opened(tmp_path, state="CLOSED")
    with pytest.raises(CommandError, match="closed without"):
        finish(sc.repo, "T-001", forge=forge)
    assert sc.state() == "NEEDS_HUMAN"


def test_a_task_with_no_recorded_pr_is_refused(tmp_path):
    sc = make_scenario(tmp_path)
    with pytest.raises(CommandError, match="no pull request"):
        finish(sc.repo, "T-001", forge=FakeForge())


def test_finishing_twice_is_refused(tmp_path):
    sc, forge = opened(tmp_path)
    finish(sc.repo, "T-001", forge=forge)
    with pytest.raises(CommandError):
        finish(sc.repo, "T-001", forge=forge)


def test_the_command_finishes_the_task(tmp_path, monkeypatch, capsys):
    sc, forge = opened(tmp_path)
    monkeypatch.setattr("adlc.cli.GhCli", lambda *a, **k: forge)
    assert main(["finish", "T-001", "--repo", str(sc.repo)]) == 0
    assert "T-001" in capsys.readouterr().out and sc.state() == "DONE"


def test_the_command_reports_an_open_pr_with_exit_1(tmp_path, monkeypatch, capsys):
    sc, forge = opened(tmp_path, state="OPEN")
    monkeypatch.setattr("adlc.cli.GhCli", lambda *a, **k: forge)
    assert main(["finish", "T-001", "--repo", str(sc.repo)]) == 1
    assert "still open" in capsys.readouterr().err
