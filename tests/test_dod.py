"""Running Definition of Done commands and recording what really happened (ADLC-011)."""

import time
from pathlib import Path

import pytest

from adlc.commands import CommandError
from adlc.dod import CommandResult, find_bash, run_all, run_command

BASH = find_bash()


# --- finding Git Bash, never WSL's bash ---------------------------------------------------------


def make_git_tree(root: Path, layout: str) -> Path:
    """A fake Git for Windows folder; returns the path of its git.exe."""
    git = root / "Git" / layout / "git.exe"
    git.parent.mkdir(parents=True)
    git.write_text("")
    return git


def test_on_windows_bash_is_found_next_to_git_not_on_the_path(tmp_path):
    git = make_git_tree(tmp_path, "cmd")
    (tmp_path / "Git" / "bin").mkdir()
    real = tmp_path / "Git" / "bin" / "bash.exe"
    real.write_text("")
    where = {"git": str(git), "bash": r"C:\Windows\System32\bash.exe"}
    assert find_bash(windows=True, which=where.get, environ={}) == str(real)


def test_git_in_mingw64_still_leads_to_the_same_bash(tmp_path):
    git = make_git_tree(tmp_path, "mingw64/bin")
    (tmp_path / "Git" / "bin").mkdir()
    real = tmp_path / "Git" / "bin" / "bash.exe"
    real.write_text("")
    assert find_bash(windows=True, which={"git": str(git)}.get, environ={}) == str(real)


def test_the_usr_bin_copy_is_used_when_bin_has_none(tmp_path):
    git = make_git_tree(tmp_path, "cmd")
    (tmp_path / "Git" / "usr" / "bin").mkdir(parents=True)
    real = tmp_path / "Git" / "usr" / "bin" / "bash.exe"
    real.write_text("")
    assert find_bash(windows=True, which={"git": str(git)}.get, environ={}) == str(real)


def test_on_windows_without_git_bash_it_stops_and_never_uses_the_path(tmp_path):
    where = {"bash": r"C:\Windows\System32\bash.exe"}
    with pytest.raises(CommandError, match="ADLC_BASH"):
        find_bash(windows=True, which=where.get, environ={})


def test_adlc_bash_wins_when_it_exists(tmp_path):
    chosen = tmp_path / "my-bash"
    chosen.write_text("")
    assert find_bash(windows=True, which=lambda n: None, environ={"ADLC_BASH": str(chosen)}) == str(
        chosen
    )


def test_adlc_bash_pointing_nowhere_is_an_error(tmp_path):
    with pytest.raises(CommandError, match="ADLC_BASH"):
        find_bash(windows=False, which=lambda n: "/bin/bash", environ={"ADLC_BASH": "/nope/bash"})


def test_elsewhere_bash_comes_from_the_path():
    assert (
        find_bash(windows=False, which={"bash": "/usr/bin/bash"}.get, environ={}) == "/usr/bin/bash"
    )
    with pytest.raises(CommandError, match="bash"):
        find_bash(windows=False, which=lambda n: None, environ={})


def test_the_bash_used_by_these_tests_exists():
    assert Path(BASH).is_file()


# --- running one command ------------------------------------------------------------------------


def run(tmp_path: Path, command: str, timeout: float = 30, name: str = "step") -> CommandResult:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return run_command(name, command, work, tmp_path / "out", timeout, BASH)


def read(tmp_path: Path, name: str) -> str:
    return (tmp_path / "out" / name).read_bytes().decode()


def test_exit_zero_is_a_pass(tmp_path):
    result = run(tmp_path, "echo hello")
    assert (result.status, result.exit_code, result.attempts, result.flaky) == ("pass", 0, 1, False)
    assert read(tmp_path, result.out).strip() == "hello"


def test_the_real_exit_code_is_recorded(tmp_path):
    result = run(tmp_path, "exit 7")
    assert (result.status, result.exit_code) == ("fail", 7)


def test_a_command_that_prints_success_but_exits_non_zero_is_a_failure(tmp_path):
    result = run(tmp_path, "echo ALL TESTS PASSED; echo OK; exit 1")
    assert result.status == "fail" and result.exit_code == 1
    assert "ALL TESTS PASSED" in read(tmp_path, result.out)


def test_noisy_output_with_exit_zero_passes(tmp_path):
    result = run(tmp_path, "for i in 1 2 3 4 5; do echo error: not really; echo oops >&2; done")
    assert result.status == "pass"
    assert "oops" in read(tmp_path, result.err) and "error" in read(tmp_path, result.out)


def test_stdout_and_stderr_are_kept_apart(tmp_path):
    result = run(tmp_path, "echo to-out; echo to-err >&2")
    assert read(tmp_path, result.out).strip() == "to-out"
    assert read(tmp_path, result.err).strip() == "to-err"


def test_it_runs_in_the_given_folder(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "marker.txt").write_text("x")
    assert run(tmp_path, "test -f marker.txt").status == "pass"


def test_a_failed_command_is_run_once_more_and_a_pass_is_flaky(tmp_path):
    command = "if test -f seen; then exit 0; fi; touch seen; echo first >&2; exit 1"
    result = run(tmp_path, command)
    assert (result.status, result.exit_code, result.attempts, result.flaky) == ("pass", 0, 2, True)
    assert "first" in read(tmp_path, "step.attempt1.err")


def test_a_command_that_fails_twice_is_a_failure_and_keeps_both_outputs(tmp_path):
    result = run(tmp_path, "echo run; exit 2")
    assert (result.status, result.attempts, result.flaky) == ("fail", 2, False)
    assert (tmp_path / "out" / "step.attempt1.out").is_file()
    assert read(tmp_path, result.out).strip() == "run"


def test_a_slow_failure_is_not_retried(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    command = "sleep 0.4; echo x >> count.txt; exit 1"
    result = run_command("step", command, work, tmp_path / "out", 30, BASH, retry_under=0.2)
    assert (result.status, result.attempts, result.flaky) == ("fail", 1, False)
    assert (work / "count.txt").read_text().count("x") == 1


def test_a_quick_failure_is_still_retried_under_the_limit(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    result = run_command("step", "exit 3", work, tmp_path / "out", 30, BASH, retry_under=60)
    assert (result.status, result.attempts) == ("fail", 2)


def test_run_all_passes_the_retry_limit_on(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    dod = {"slow": "sleep 0.4; exit 1"}
    [slow] = run_all(dod, work, tmp_path / "out", bash=BASH, retry_under=0.2)
    assert slow.attempts == 1


def test_a_passing_command_is_run_only_once(tmp_path):
    result = run(tmp_path, "echo x >> count.txt")
    assert result.attempts == 1
    assert (tmp_path / "work" / "count.txt").read_text().count("x") == 1


def test_a_hung_command_is_killed_and_not_retried(tmp_path):
    started = time.monotonic()
    result = run(tmp_path, "sleep 60", timeout=0.5)
    assert time.monotonic() - started < 20
    assert (result.status, result.exit_code, result.attempts) == ("timeout", None, 1)


def test_the_duration_is_measured(tmp_path):
    result = run(tmp_path, "sleep 0.3")
    assert 0.2 <= result.duration < 20


# --- running the whole list ---------------------------------------------------------------------

DOD = {"first": "echo a", "second": "exit 4", "third": "echo c"}


def run_list(tmp_path: Path, **kwargs):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return run_all(DOD, work, tmp_path / "out", bash=BASH, timeout=30, **kwargs)


def test_all_commands_run_in_order_and_one_failure_does_not_stop_the_rest(tmp_path):
    results = run_list(tmp_path)
    assert [(r.name, r.status) for r in results] == [
        ("first", "pass"),
        ("second", "fail"),
        ("third", "pass"),
    ]


def test_only_and_skip_mark_the_other_commands_skipped(tmp_path):
    only = run_list(tmp_path, only=["second"])
    assert [r.status for r in only] == ["skipped", "fail", "skipped"]
    skip = run_list(tmp_path, skip=["second"])
    assert [r.status for r in skip] == ["pass", "skipped", "pass"]
    skipped = skip[1]
    assert (skipped.exit_code, skipped.attempts, skipped.duration) == (None, 0, 0)


def test_unknown_names_are_refused(tmp_path):
    with pytest.raises(CommandError, match="nope"):
        run_list(tmp_path, only=["nope"])
    with pytest.raises(CommandError, match="nope"):
        run_list(tmp_path, skip=["nope"])


def test_an_empty_list_is_refused(tmp_path):
    with pytest.raises(CommandError, match="profile"):
        run_all({}, tmp_path, tmp_path / "out", bash=BASH, timeout=1)


def test_progress_is_reported_before_each_command(tmp_path):
    seen = []
    run_list(tmp_path, progress=seen.append)
    assert seen == ["first", "second", "third"]


def test_bin_bash_is_preferred_over_usr_bin_bash(tmp_path):
    git = make_git_tree(tmp_path, "cmd")
    for folder in ("bin", "usr/bin"):
        (tmp_path / "Git" / folder).mkdir(parents=True)
        (tmp_path / "Git" / folder / "bash.exe").write_text("")
    got = find_bash(windows=True, which={"git": str(git)}.get, environ={})
    assert Path(got) == tmp_path / "Git" / "bin" / "bash.exe"
