"""The baseline of a repo's Definition of Done results, and comparing a task run with it."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from adlc.baseline import (
    Baseline,
    Verdict,
    blocked,
    compare,
    load_baseline,
    take_baseline,
)
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.dod import CommandResult, find_bash, run_all

BASH = find_bash()
STORE = ".adlc/logs/baseline"


def make_repo(tmp_path: Path, dod: dict) -> Path:
    repo = tmp_path / "repo"
    (repo / ".adlc").mkdir(parents=True)
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": dod}))
    return repo


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit_all(repo: Path) -> str:
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return git(repo, "rev-parse", "HEAD")


def take(repo: Path, **kwargs) -> Baseline:
    return take_baseline(repo, load_config(repo), bash=BASH, timeout=30, **kwargs)


DOD = {"build": "echo built", "lint": "exit 3", "test": "echo ok"}


# --- taking a baseline --------------------------------------------------------------------------


def test_a_baseline_records_every_command_and_is_saved(tmp_path):
    repo = make_repo(tmp_path, DOD)
    baseline = take(repo)
    assert [(c.name, c.status) for c in baseline.commands] == [
        ("build", "pass"),
        ("lint", "fail"),
        ("test", "pass"),
    ]
    folder = repo / STORE
    assert (folder / "baseline.json").is_file()
    assert (folder / "build.out").read_bytes().decode().strip() == "built"
    assert (folder / "lint.err").is_file()


def test_the_saved_file_has_the_agreed_fields(tmp_path):
    repo = make_repo(tmp_path, DOD)
    take(repo)
    data = json.loads((repo / STORE / "baseline.json").read_text())
    assert data["schema"] == 1
    assert set(data) == {"schema", "taken_at", "commit", "dirty", "platform", "commands"}
    first = data["commands"][0]
    assert set(first) == {
        "name", "command", "status", "exit_code", "duration", "attempts", "flaky", "out", "err",
    }  # fmt: skip
    assert first["name"] == "build" and first["command"] == "echo built"


def test_the_saved_baseline_loads_back_unchanged(tmp_path):
    repo = make_repo(tmp_path, DOD)
    taken = take(repo)
    assert load_baseline(repo) == taken


def test_outside_git_there_is_no_commit_and_no_dirty_flag(tmp_path):
    repo = make_repo(tmp_path, DOD)
    baseline = take(repo)
    assert (baseline.commit, baseline.dirty) == (None, None)


def test_in_git_the_commit_is_recorded(tmp_path):
    repo = make_repo(tmp_path, DOD)
    sha = commit_all(repo)
    baseline = take(repo)
    assert (baseline.commit, baseline.dirty) == (sha, False)


def test_a_dirty_tree_is_refused_unless_allowed(tmp_path):
    repo = make_repo(tmp_path, DOD)
    (repo / "tracked.txt").write_text("one")
    commit_all(repo)
    (repo / "tracked.txt").write_text("two")
    with pytest.raises(CommandError, match="--allow-dirty"):
        take(repo)
    assert not (repo / STORE).exists()
    assert take(repo, allow_dirty=True).dirty is True


def test_untracked_files_do_not_make_the_tree_dirty(tmp_path):
    repo = make_repo(tmp_path, DOD)
    commit_all(repo)
    (repo / "new.txt").write_text("untracked")
    assert take(repo).dirty is False


def test_a_new_baseline_replaces_the_old_one(tmp_path):
    repo = make_repo(tmp_path, {"build": "echo one", "extra": "echo two"})
    take(repo)
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": {"build": "echo three"}}))
    take(repo)
    assert [c.name for c in load_baseline(repo).commands] == ["build"]
    assert not (repo / STORE / "extra.out").exists()


def test_only_and_skip_still_list_every_command(tmp_path):
    repo = make_repo(tmp_path, DOD)
    baseline = take(repo, only=["build"])
    assert [(c.name, c.status) for c in baseline.commands] == [
        ("build", "pass"),
        ("lint", "skipped"),
        ("test", "skipped"),
    ]


def test_no_commands_is_an_error_that_points_to_a_profile(tmp_path):
    repo = make_repo(tmp_path, {})
    with pytest.raises(CommandError, match="profile"):
        take(repo)


def test_progress_names_each_command(tmp_path):
    repo = make_repo(tmp_path, DOD)
    seen = []
    take(repo, progress=seen.append)
    assert seen == ["build", "lint", "test"]


# --- loading ------------------------------------------------------------------------------------


def test_no_baseline_loads_as_none(tmp_path):
    assert load_baseline(make_repo(tmp_path, DOD)) is None


FOREIGN = json.dumps(
    {
        "schema": 99,
        "taken_at": "t",
        "commit": None,
        "dirty": None,
        "platform": "x",
        "commands": [],
    }
)


@pytest.mark.parametrize("text", ["not json", "[]", FOREIGN, "{}"])
def test_a_damaged_or_foreign_baseline_is_an_error(tmp_path, text):
    repo = make_repo(tmp_path, DOD)
    (repo / STORE).mkdir(parents=True)
    (repo / STORE / "baseline.json").write_text(text)
    with pytest.raises(CommandError, match="baseline"):
        load_baseline(repo)


# --- comparing a task run with the baseline -----------------------------------------------------


def result(name: str, status: str) -> CommandResult:
    code = {"pass": 0, "fail": 1, "timeout": None, "skipped": None}[status]
    return CommandResult(
        name, "x", status, code, 0.0, 0 if status == "skipped" else 1, False, "", ""
    )


def baseline_of(**statuses: str) -> Baseline:
    commands = tuple(result(name, status) for name, status in statuses.items())
    return Baseline(1, "2026-10-29T00:00:00+00:00", None, None, "test", commands)


def verdicts(now: dict, before: dict) -> dict[str, str]:
    got = compare([result(n, s) for n, s in now.items()], baseline_of(**before))
    return {v.name: v.verdict for v in got}


def test_passing_stays_pass_and_a_fix_is_reported():
    got = verdicts({"a": "pass", "b": "pass"}, {"a": "pass", "b": "fail"})
    assert got == {"a": "pass", "b": "fixed"}


def test_a_failure_that_was_already_there_is_pre_existing():
    assert verdicts({"a": "fail", "b": "timeout"}, {"a": "fail", "b": "timeout"}) == {
        "a": "pre-existing",
        "b": "pre-existing",
    }
    assert verdicts({"a": "timeout"}, {"a": "fail"}) == {"a": "pre-existing"}


def test_a_failure_where_the_baseline_passed_is_new():
    assert verdicts({"a": "fail"}, {"a": "pass"}) == {"a": "new"}
    assert verdicts({"a": "timeout"}, {"a": "pass"}) == {"a": "new"}


def test_a_failure_the_baseline_never_judged_is_new():
    assert verdicts({"a": "fail"}, {"a": "skipped"}) == {"a": "new"}
    assert verdicts({"a": "fail", "z": "fail"}, {"a": "fail"})["z"] == "new"


def test_a_skipped_command_is_not_judged():
    assert verdicts({"a": "skipped"}, {"a": "fail"}) == {"a": "skipped"}


def test_only_new_failures_block():
    mixed = compare([result("a", "fail"), result("b", "pass")], baseline_of(a="fail", b="pass"))
    assert not blocked(mixed)
    worse = compare([result("a", "fail"), result("b", "fail")], baseline_of(a="fail", b="pass"))
    assert blocked(worse)
    assert not blocked([])


def test_with_no_baseline_every_failure_is_new():
    got = compare([result("a", "fail"), result("b", "pass")], None)
    assert [(v.name, v.verdict) for v in got] == [("a", "new"), ("b", "pass")]


def test_a_verdict_carries_both_statuses():
    (got,) = compare([result("a", "fail")], baseline_of(a="pass"))
    assert got == Verdict("a", "new", "fail", "pass")


# --- the acceptance test: a command that fails only after a change ------------------------------


def test_a_command_that_starts_failing_after_a_change_is_new(tmp_path):
    repo = make_repo(tmp_path, {"check": "test ! -f broken.flag", "lint": "exit 1"})
    baseline = take(repo)
    assert load_baseline(repo) == baseline
    (repo / "broken.flag").write_text("the change")
    now = run_all(load_config(repo).dod, repo, tmp_path / "task-run", bash=BASH, timeout=30)
    got = {v.name: v.verdict for v in compare(now, load_baseline(repo))}
    assert got == {"check": "new", "lint": "pre-existing"}
    assert blocked(compare(now, load_baseline(repo)))


def test_a_baseline_where_everything_is_skipped_is_still_saved(tmp_path):
    repo = make_repo(tmp_path, DOD)
    baseline = take(repo, only=["build"], skip=["build"])
    assert {c.status for c in baseline.commands} == {"skipped"}
    assert load_baseline(repo) == baseline
