"""Pause, resume, reset, handoff and stuck tasks (ADLC-022)."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adlc.commands import CommandError, status_report
from adlc.config import load_config
from adlc.escalation import Escalation, Trigger, list_open, raise_escalation
from adlc.fake_runner import rate_limited
from adlc.loop import LoopOutcome, Stop
from adlc.recovery import (
    WINDOW,
    can_start,
    current_pause,
    due_pauses,
    handoff,
    note_limit_hit,
    pause_from_outcome,
    pause_hook,
    pause_task,
    record_start,
    reset,
    resume,
)
from adlc.states import IllegalTransition
from test_commands import make_repo
from test_loop import make_scenario as make_loop_scenario
from test_pr import g, make_scenario

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@pytest.fixture
def sc(tmp_path):
    return make_scenario(tmp_path)


def status(sc):
    return sc.state()


def outcome(stop, detail="why", resets_at=None):
    return LoopOutcome(stop, detail, (), 0, "", resets_at, Path("run-1/loop.json"))


# --- a usage-window pause -----------------------------------------------------------------------


def test_pausing_marks_paused_in_notes_and_keeps_the_state(sc):
    pause = pause_task(sc.repo, "T-001", resets_at=LATER, detail="usage limit reached", now=NOW)
    assert status(sc) == "IN_PROGRESS"
    assert sc.notes().startswith("PAUSED")
    assert "usage limit reached" in sc.notes()
    assert pause.resets_at == LATER
    assert current_pause(sc.repo, "T-001").resets_at == LATER


def test_pausing_raises_no_escalation_and_no_failure(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    assert list_open(sc.repo) == []
    assert status(sc) != "FAILED"


def test_an_unknown_reset_time_falls_back_to_one_window(sc):
    pause = pause_task(sc.repo, "T-001", resets_at=None, now=NOW)
    assert pause.resets_at == NOW + WINDOW


def test_only_a_task_in_progress_can_be_paused(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "BACKLOG")])
    with pytest.raises(CommandError, match="IN_PROGRESS"):
        pause_task(repo, "T-001", resets_at=LATER, now=NOW)


def test_a_paused_loop_outcome_pauses_the_task(sc):
    pause = pause_from_outcome(sc.repo, "T-001", outcome(Stop.PAUSED, "limit", LATER), now=NOW)
    assert pause is not None
    assert sc.notes().startswith("PAUSED")


@pytest.mark.parametrize("stop", [Stop.SUCCESS, Stop.STUCK, Stop.MAX_ITERATIONS])
def test_any_other_outcome_leaves_the_task_alone(sc, stop):
    assert pause_from_outcome(sc.repo, "T-001", outcome(stop), now=NOW) is None
    assert current_pause(sc.repo, "T-001") is None
    assert not sc.notes().startswith("PAUSED")


def test_a_real_usage_limit_pauses_the_loop_without_counting_an_iteration(tmp_path):
    loop = make_loop_scenario(tmp_path / "loop")
    repo = make_repo(tmp_path / "board", [("T-001", "IN_PROGRESS")])
    got, _ = loop.run([rate_limited(LATER)], key="T-001")
    assert got.stop is Stop.PAUSED
    assert got.iterations == ()  # the interrupted call counted no iteration
    pause = pause_from_outcome(repo, "T-001", got, now=NOW)
    assert pause.resets_at == LATER
    assert list_open(repo) == []


def test_the_pause_hook_records_the_reset_time_from_the_provider(sc):
    hook = pause_hook(sc.repo, "T-001", now=NOW)
    hook(rate_limited(LATER))
    assert current_pause(sc.repo, "T-001").resets_at == LATER


def test_pauses_are_due_only_after_the_window_resets(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    assert due_pauses(sc.repo, NOW + timedelta(hours=1)) == []
    assert [p.key for p in due_pauses(sc.repo, LATER)] == ["T-001"]


# --- max_tasks_per_window -----------------------------------------------------------------------


@pytest.fixture
def limited(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "BACKLOG")])
    return repo, load_config(repo)  # max_tasks_per_window defaults to 3


def test_a_window_allows_the_configured_number_of_starts(limited):
    repo, settings = limited
    for i in range(3):
        assert can_start(repo, settings, NOW)
        record_start(repo, f"K-{i}", NOW)
    assert not can_start(repo, settings, NOW)


def test_a_start_is_counted_once_per_task(limited):
    repo, settings = limited
    for _ in range(5):
        record_start(repo, "K-1", NOW)
    assert can_start(repo, settings, NOW)


def test_old_starts_fall_out_of_the_window(limited):
    repo, settings = limited
    for i in range(3):
        record_start(repo, f"K-{i}", NOW)
    assert can_start(repo, settings, NOW + WINDOW + timedelta(minutes=1))


def test_a_limit_hit_ends_the_window_when_it_resets(limited):
    repo, settings = limited
    for i in range(3):
        record_start(repo, f"K-{i}", NOW)
    note_limit_hit(repo, LATER)
    assert not can_start(repo, settings, NOW + timedelta(hours=1))
    assert can_start(repo, settings, LATER + timedelta(minutes=1))


# --- resume -------------------------------------------------------------------------------------


def test_resume_gives_back_the_carried_state(sc):
    (sc.worktree / "progress.md").write_text("tried X\n")
    got = resume(sc.repo, "T-001", now=NOW)
    assert got.key == "T-001"
    assert got.worktree.resolve() == sc.worktree.resolve()
    assert got.branch.startswith("feature/")
    assert got.progress is not None
    assert not got.was_paused


def test_resume_waits_for_the_window_to_reset(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    with pytest.raises(CommandError, match="paused until"):
        resume(sc.repo, "T-001", now=NOW + timedelta(minutes=5))
    assert sc.notes().startswith("PAUSED")


def test_resume_after_the_reset_clears_the_pause(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    got = resume(sc.repo, "T-001", now=LATER + timedelta(minutes=1))
    assert got.was_paused
    assert current_pause(sc.repo, "T-001") is None
    assert not sc.notes().startswith("PAUSED")
    assert status(sc) == "IN_PROGRESS"


def test_force_resumes_before_the_reset(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    assert resume(sc.repo, "T-001", now=NOW, force=True).was_paused


def test_resume_honours_max_tasks_per_window(sc):
    for i in range(3):
        record_start(sc.repo, f"K-{i}", NOW)
    with pytest.raises(CommandError, match="max_tasks_per_window"):
        resume(sc.repo, "T-001", now=NOW)
    assert resume(sc.repo, "T-001", now=NOW, force=True)


def test_resuming_counts_as_a_start_in_the_new_window(sc):
    resume(sc.repo, "T-001", now=NOW)
    starts = json.loads((sc.repo / ".adlc" / "window.json").read_text())["starts"]
    assert [s["key"] for s in starts] == ["T-001"]


@pytest.mark.parametrize("state", ["BACKLOG", "NEEDS_HUMAN", "DONE"])
def test_only_an_in_progress_task_can_be_resumed(tmp_path, state):
    repo = make_repo(tmp_path, [("T-001", state)])
    with pytest.raises(CommandError, match=state):
        resume(repo, "T-001", now=NOW)


def test_resume_without_a_worktree_points_to_reset(sc):
    g(sc.repo, "worktree", "remove", "--force", str(sc.worktree))
    with pytest.raises(CommandError, match="reset"):
        resume(sc.repo, "T-001", now=NOW)


# --- reset --------------------------------------------------------------------------------------


def test_reset_needs_an_explicit_yes_and_changes_nothing_without_it(sc):
    with pytest.raises(CommandError, match="--yes"):
        reset(sc.repo, "T-001")
    assert sc.worktree.exists()
    assert status(sc) == "IN_PROGRESS"


def test_reset_clears_the_carried_state_and_returns_the_task_to_backlog(sc):
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    (sc.worktree / "progress.md").write_text("tried X\n")
    done = reset(sc.repo, "T-001", yes=True)
    assert done.worktree_removed
    assert done.branch_deleted
    assert not sc.worktree.exists()
    assert status(sc) == "BACKLOG"
    assert current_pause(sc.repo, "T-001") is None
    assert "Reset" in sc.notes()


def test_reset_keeps_the_run_logs(sc):
    log = sc.repo / ".adlc" / "logs" / "T-001" / "run-1"
    log.mkdir(parents=True)
    (log / "summary.md").write_text("what happened")
    reset(sc.repo, "T-001", yes=True)
    assert (log / "summary.md").read_text() == "what happened"


def test_reset_drops_an_open_escalation(sc):
    raise_escalation(sc.repo, "T-001", Escalation(Trigger.LOOP_EXHAUSTED, "gave up"))
    reset(sc.repo, "T-001", yes=True)
    assert list_open(sc.repo) == []
    assert status(sc) == "BACKLOG"


def test_reset_refuses_while_a_pr_is_open(sc):
    pr = sc.repo / ".adlc" / "prs"
    pr.mkdir(parents=True)
    (pr / "T-001.json").write_text(json.dumps({"number": 7, "url": "u", "branch": "b"}))
    with pytest.raises(CommandError, match="PR"):
        reset(sc.repo, "T-001", yes=True)
    assert sc.worktree.exists()


@pytest.mark.parametrize("state", ["BACKLOG", "DONE", "DRAFT"])
def test_reset_only_applies_to_a_task_that_has_started(tmp_path, state):
    repo = make_repo(tmp_path, [("T-001", state)])
    with pytest.raises((CommandError, IllegalTransition)):
        reset(repo, "T-001", yes=True)


# --- handoff ------------------------------------------------------------------------------------


def write_run(sc, **record):
    folder = sc.repo / ".adlc" / "logs" / "T-001" / "run-1"
    folder.mkdir(parents=True)
    (folder / "summary.md").write_text("summary")
    data = {"stop": "stuck", "detail": "same failure twice", "last_signature": "test: E assert 1"}
    (folder / "loop.json").write_text(json.dumps({**data, **record}))
    return folder


def test_handoff_links_the_log_the_branch_and_the_last_failure(sc):
    write_run(sc)
    path = handoff(sc.repo, "T-001", now=NOW)
    text = path.read_text()
    assert path == sc.repo / ".adlc" / "handoff" / "T-001.md"
    assert "T-001" in text
    assert "feature/" in text  # the branch
    assert ".adlc/logs/T-001/run-1/summary.md" in text
    assert "same failure twice" in text
    assert "test: E assert 1" in text
    assert "adlc resume T-001" in text  # the suggested next step


def test_handoff_links_artifacts_instead_of_copying_them(sc):
    folder = write_run(sc)
    (folder / "summary.md").write_text("UNIQUE-SUMMARY-BODY " * 50)
    assert "UNIQUE-SUMMARY-BODY" not in handoff(sc.repo, "T-001", now=NOW).read_text()


def test_handoff_redacts_secrets(sc):
    write_run(sc, detail="failed with token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    text = handoff(sc.repo, "T-001", now=NOW).read_text()
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in text
    assert "[REDACTED]" in text


def test_handoff_for_a_paused_task_says_when_it_resumes(sc):
    write_run(sc)
    pause_task(sc.repo, "T-001", resets_at=LATER, now=NOW)
    assert LATER.isoformat() in handoff(sc.repo, "T-001", now=NOW).read_text()


def test_handoff_for_an_escalated_task_carries_the_trigger_and_next_step(sc):
    write_run(sc)
    raise_escalation(
        sc.repo, "T-001", Escalation(Trigger.TASK_FLAG, "needs sign-off", (), "approve it")
    )
    text = handoff(sc.repo, "T-001", now=NOW).read_text()
    assert "task_flag" in text
    assert "approve it" in text


def test_handoff_without_any_log_still_works(sc):
    text = handoff(sc.repo, "T-001", now=NOW).read_text()
    assert "no run log" in text


def test_handoff_for_an_unknown_task_is_refused(sc):
    with pytest.raises(CommandError, match="T-999"):
        handoff(sc.repo, "T-999", now=NOW)


# --- adlc status flags a stuck task -------------------------------------------------------------


def age(folder: Path, minutes: float, now: datetime) -> None:
    stamp = (now - timedelta(minutes=minutes)).timestamp()
    for path in [folder, *folder.rglob("*")]:
        os.utime(path, (stamp, stamp))


def stuck_repo(tmp_path, minutes):
    repo = make_repo(tmp_path, [("T-001", "IN_PROGRESS"), ("T-002", "IN_PROGRESS")])
    for key in ("T-001", "T-002"):
        folder = repo / ".adlc" / "logs" / key / "run-1"
        folder.mkdir(parents=True)
        (folder / "loop.json").write_text("{}")
        age(folder, minutes if key == "T-001" else 1, NOW)
    return repo


def item(report, key):
    return next(i for i in report["items"] if i["key"] == key)


def test_status_flags_an_in_progress_task_with_no_recent_activity(tmp_path):
    repo = stuck_repo(tmp_path, minutes=100)  # the limit is 2 x max_task_minutes = 60
    report = status_report(repo, now=NOW)
    assert item(report, "T-001")["group"] == "attention"
    assert "no activity" in item(report, "T-001")["reason"]
    assert "adlc resume" in item(report, "T-001")["reason"]
    assert item(report, "T-002")["group"] == "in_flight"


def test_a_task_inside_the_limit_is_not_flagged(tmp_path):
    repo = stuck_repo(tmp_path, minutes=50)
    assert item(status_report(repo, now=NOW), "T-001")["group"] == "in_flight"


def test_a_paused_task_is_not_stuck(tmp_path):
    repo = stuck_repo(tmp_path, minutes=500)
    pause_task(repo, "T-001", resets_at=LATER, now=NOW)
    assert item(status_report(repo, now=NOW), "T-001")["group"] == "in_flight"


def test_a_task_with_no_run_log_is_not_guessed_stuck(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "IN_PROGRESS")])
    assert item(status_report(repo, now=NOW), "T-001")["group"] == "in_flight"
