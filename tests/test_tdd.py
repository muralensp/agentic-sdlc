"""The Tester's red phase and the lock on its tests (ADLC-015)."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from adlc.commands import CommandError
from adlc.config import load_config
from adlc.dod import find_bash
from adlc.tdd import (
    Lock,
    RedCheck,
    Violation,
    changed_paths,
    check_locked,
    load_lock,
    lock_tests,
    out_of_scope_for_tester,
    proposed_test_change,
    red_check,
    restore_locked,
)

BASH = find_bash()

# A "test" here is a shell script: it prints FAIL and exits 1 to be red, exits 0 to be green.
RED_FILE = "echo 'FAIL: not built yet'; exit 1"
GREEN_FILE = "echo fine; exit 0"
BROKEN_FILE = "echo 'syntax error'; exit 2"


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def make_settings(tmp_path: Path, tests: dict | None = None, protected_extra=()):
    primary = tmp_path / "primary"
    (primary / ".adlc").mkdir(parents=True, exist_ok=True)
    own = {
        "dod": {"lint": "echo ok", "test": "echo FAIL; exit 1"},
        "tests": {
            "paths": ["tests/**"],
            "red": "bash {file}",
            "failure_markers": ["FAIL"],
            **(tests or {}),
        },
        "protected_extra": list(protected_extra),
    }
    (primary / ".adlc" / "config.yaml").write_text(yaml.safe_dump(own, sort_keys=False))
    return load_config(primary)


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir()
    (work / "src" / "app.py").write_text("x = 1\n")
    g(work.parent, "init", "-q", "-b", "main", str(work))
    g(work, "add", "-A")
    g(work, "commit", "-q", "-m", "init")
    return work


def write(work: Path, rel: str, text: str) -> str:
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return rel


# --- the config block ---------------------------------------------------------------------------


def test_the_tests_block_is_read(tmp_path):
    s = make_settings(tmp_path)
    assert s.tests.paths == ("tests/**",)
    assert s.tests.red == "bash {file}"
    assert s.tests.failure_markers == ("FAIL",)


def test_a_misspelt_tests_key_is_refused(tmp_path):
    primary = tmp_path / "primary"
    (primary / ".adlc").mkdir(parents=True)
    (primary / ".adlc" / "config.yaml").write_text("tests:\n  path: [a]\n")
    from adlc.config import ConfigError

    with pytest.raises(ConfigError, match="tests.path"):
        load_config(primary)


def test_the_generic_profile_has_no_test_paths(tmp_path):
    assert load_config(tmp_path).tests.paths == ()


def test_the_python_profile_knows_python_tests(tmp_path):
    (tmp_path / ".adlc").mkdir()
    (tmp_path / ".adlc" / "config.yaml").write_text("extends: python\n")
    t = load_config(tmp_path).tests
    assert "tests/**" in t.paths and "**/conftest.py" in t.paths
    assert "{file}" in t.red and "FAILED" in t.failure_markers


def test_the_dotnet_profile_knows_dotnet_tests(tmp_path):
    (tmp_path / ".adlc").mkdir()
    (tmp_path / ".adlc" / "config.yaml").write_text("extends: dotnet\n")
    t = load_config(tmp_path).tests
    assert "**/*.Tests/**" in t.paths and t.red is None and "Failed" in t.failure_markers


# --- what the Tester changed --------------------------------------------------------------------


def test_changed_paths_lists_added_modified_and_deleted(repo):
    write(repo, "tests/gone.sh", "x")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "more")
    base = g(repo, "rev-parse", "HEAD")
    (repo / "tests" / "gone.sh").unlink()
    write(repo, "src/app.py", "x = 2\n")
    write(repo, "tests/untracked.sh", RED_FILE)
    write(repo, "tests/staged.sh", RED_FILE)
    g(repo, "add", "tests/staged.sh")
    assert changed_paths(repo, base) == {
        "tests/gone.sh": "D",
        "src/app.py": "M",
        "tests/untracked.sh": "A",
        "tests/staged.sh": "A",
    }


def test_nothing_changed_is_empty(repo):
    assert changed_paths(repo, "HEAD") == {}


def test_the_tester_may_change_only_tests(tmp_path, repo):
    s = make_settings(tmp_path)
    changes = {"tests/a.sh": "A", "src/app.py": "M", "docs/WORK-ITEMS.md": "M"}
    assert out_of_scope_for_tester(changes, s) == ["src/app.py", "docs/WORK-ITEMS.md"]


def test_a_protected_test_path_is_still_off_limits(tmp_path):
    s = make_settings(tmp_path, protected_extra=["tests/golden/**"])
    assert out_of_scope_for_tester({"tests/golden/x.sh": "M", "tests/a.sh": "A"}, s) == [
        "tests/golden/x.sh"
    ]


def test_without_test_globs_the_tester_may_change_nothing(tmp_path):
    s = make_settings(tmp_path, tests={"paths": []})
    assert out_of_scope_for_tester({"tests/a.sh": "A"}, s) == ["tests/a.sh"]


# --- the red check ------------------------------------------------------------------------------


def red(tmp_path, repo, files, **kwargs):
    s = make_settings(tmp_path, kwargs.pop("tests", None))
    return red_check(repo, s, tmp_path / "out", files, bash=BASH, timeout=30, **kwargs)


def test_a_failing_test_is_red_and_the_phase_passes(tmp_path, repo):
    f = write(repo, "tests/a.sh", RED_FILE)
    r = red(tmp_path, repo, [f])
    assert [(x.path, x.verdict) for x in r.files] == [("tests/a.sh", "red")]
    assert r.ok


def test_a_test_that_already_passes_is_rejected(tmp_path, repo):
    files = [write(repo, "tests/a.sh", RED_FILE), write(repo, "tests/b.sh", GREEN_FILE)]
    r = red(tmp_path, repo, files)
    assert {x.path: x.verdict for x in r.files} == {"tests/a.sh": "red", "tests/b.sh": "passes"}
    assert not r.ok
    assert r.passing == ("tests/b.sh",)


def test_a_non_zero_exit_without_a_failure_marker_is_broken(tmp_path, repo):
    f = write(repo, "tests/a.sh", BROKEN_FILE)
    r = red(tmp_path, repo, [f])
    assert r.files[0].verdict == "broken" and r.files[0].exit_code == 2
    assert not r.ok and r.broken == ("tests/a.sh",)


def test_the_marker_may_be_on_stderr(tmp_path, repo):
    f = write(repo, "tests/a.sh", "echo 'FAIL' >&2; exit 1")
    assert red(tmp_path, repo, [f]).ok


def test_the_marker_match_ignores_case(tmp_path, repo):
    f = write(repo, "tests/a.sh", "echo 'a test failed'; exit 1")
    assert red(tmp_path, repo, [f], tests={"failure_markers": ["FAILED"]}).ok


def test_a_hung_test_is_broken_not_red(tmp_path, repo):
    f = write(repo, "tests/a.sh", "sleep 60")
    s = make_settings(tmp_path)
    r = red_check(repo, s, tmp_path / "out", [f], bash=BASH, timeout=0.5)
    assert r.files[0].verdict == "broken" and not r.ok


def test_the_command_runs_in_the_worktree_with_the_file_quoted(tmp_path, repo):
    f = write(repo, "tests/with space.sh", "pwd > where.txt; echo FAIL; exit 1")
    assert red(tmp_path, repo, [f]).ok
    assert (repo / "where.txt").is_file()


def test_each_file_is_run_on_its_own(tmp_path, repo):
    a = write(repo, "tests/a.sh", "echo a >> ran.txt; echo FAIL; exit 1")
    b = write(repo, "tests/b.sh", "echo b >> ran.txt; echo FAIL; exit 1")
    red(tmp_path, repo, [a, b])
    assert sorted((repo / "ran.txt").read_text().split()) == ["a", "b"]


def test_a_failed_test_is_not_retried(tmp_path, repo):
    f = write(repo, "tests/a.sh", "echo x >> n.txt; echo FAIL; exit 1")
    r = red(tmp_path, repo, [f])
    assert (repo / "n.txt").read_text().count("x") == 1 and r.files[0].attempts == 1


def test_without_a_red_command_the_whole_test_command_runs_once(tmp_path, repo):
    files = [write(repo, "tests/a.sh", GREEN_FILE)]
    r = red(tmp_path, repo, files, tests={"red": None})
    assert [x.path for x in r.files] == ["<all tests>"]
    assert r.files[0].verdict == "red" and r.ok  # the dod `test` command prints FAIL and exits 1


def test_the_whole_run_that_passes_is_rejected(tmp_path, repo):
    s = make_settings(tmp_path, tests={"red": None})
    object.__setattr__(s, "dod", {"lint": "echo ok", "test": "echo fine"})
    f = write(repo, "tests/a.sh", RED_FILE)
    r = red_check(repo, s, tmp_path / "out", [f], bash=BASH, timeout=30)
    assert r.files[0].verdict == "passes" and not r.ok


def test_no_files_is_an_error(tmp_path, repo):
    with pytest.raises(CommandError, match="no new or changed test"):
        red(tmp_path, repo, [])


def test_without_test_globs_the_red_phase_refuses(tmp_path, repo):
    f = write(repo, "tests/a.sh", RED_FILE)
    with pytest.raises(CommandError, match="tests.paths"):
        red(tmp_path, repo, [f], tests={"paths": []})


def test_red_json_records_every_run(tmp_path, repo):
    files = [write(repo, "tests/a.sh", RED_FILE), write(repo, "tests/b.sh", GREEN_FILE)]
    r = red(tmp_path, repo, files)
    data = json.loads(r.record.read_bytes().decode())
    assert r.record == tmp_path / "out" / "red.json"
    assert data["ok"] is False and data["schema"] == 1
    assert [(f["path"], f["verdict"], f["exit_code"]) for f in data["files"]] == [
        ("tests/a.sh", "red", 1),
        ("tests/b.sh", "passes", 0),
    ]
    assert (tmp_path / "out" / data["files"][0]["out"]).is_file()


# --- the lock -----------------------------------------------------------------------------------


def locked(tmp_path, repo):
    files = [write(repo, "tests/a.sh", RED_FILE), write(repo, "tests/b.sh", RED_FILE)]
    return lock_tests(repo, files, tmp_path / "lock")


def test_untouched_tests_have_no_violations(tmp_path, repo):
    assert check_locked(repo, locked(tmp_path, repo)) == []


def test_an_edited_test_is_reported(tmp_path, repo):
    lock = locked(tmp_path, repo)
    write(repo, "tests/a.sh", "exit 0")
    assert [(v.path, v.kind) for v in check_locked(repo, lock)] == [("tests/a.sh", "modified")]


def test_a_deleted_test_is_reported(tmp_path, repo):
    lock = locked(tmp_path, repo)
    (repo / "tests" / "b.sh").unlink()
    assert [(v.path, v.kind) for v in check_locked(repo, lock)] == [("tests/b.sh", "deleted")]


def test_a_new_test_added_by_the_coder_is_allowed(tmp_path, repo):
    lock = locked(tmp_path, repo)
    write(repo, "tests/extra.sh", GREEN_FILE)
    assert check_locked(repo, lock) == []


def test_a_one_byte_change_is_caught(tmp_path, repo):
    lock = locked(tmp_path, repo)
    with open(repo / "tests" / "a.sh", "ab") as handle:
        handle.write(b" ")
    assert len(check_locked(repo, lock)) == 1


def test_line_endings_count_as_a_change(tmp_path, repo):
    lock = locked(tmp_path, repo)
    (repo / "tests" / "a.sh").write_bytes((repo / "tests" / "a.sh").read_bytes() + b"\r\n")
    assert len(check_locked(repo, lock)) == 1


def test_the_lock_is_kept_outside_the_worktree_and_reloads(tmp_path, repo):
    locked(tmp_path, repo)
    lock = load_lock(tmp_path / "lock")
    assert sorted(lock.paths) == ["tests/a.sh", "tests/b.sh"]
    write(repo, "tests/a.sh", "exit 0")
    assert len(check_locked(repo, lock)) == 1


def test_restore_puts_back_modified_and_deleted_files(tmp_path, repo):
    lock = locked(tmp_path, repo)
    write(repo, "tests/a.sh", "exit 0")
    (repo / "tests" / "b.sh").unlink()
    bad = check_locked(repo, lock)
    restore_locked(repo, lock, bad)
    assert check_locked(repo, lock) == []
    assert (repo / "tests" / "a.sh").read_text() == RED_FILE


def test_restore_only_touches_the_violations(tmp_path, repo):
    lock = locked(tmp_path, repo)
    write(repo, "tests/extra.sh", "mine")
    write(repo, "tests/a.sh", "exit 0")
    restore_locked(repo, lock, check_locked(repo, lock))
    assert (repo / "tests" / "extra.sh").read_text() == "mine"


def test_a_missing_or_damaged_lock_is_an_error(tmp_path):
    with pytest.raises(CommandError, match="lock"):
        load_lock(tmp_path / "nothing")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "lock.json").write_text("{not json")
    with pytest.raises(CommandError, match="lock"):
        load_lock(tmp_path / "bad")


def test_locking_a_file_that_is_not_there_is_an_error(tmp_path, repo):
    with pytest.raises(CommandError, match="nope.sh"):
        lock_tests(repo, ["tests/nope.sh"], tmp_path / "lock")


def test_a_path_that_leaves_the_worktree_cannot_be_locked_or_restored(tmp_path, repo):
    with pytest.raises(CommandError, match="outside"):
        lock_tests(repo, ["../outside.sh"], tmp_path / "lock")


# --- proposals in progress.md -------------------------------------------------------------------


def test_no_heading_means_no_proposal():
    assert proposed_test_change("## Done\nall good\n") is None
    assert proposed_test_change("") is None


def test_the_proposal_text_is_returned():
    text = "# Progress\n\n## Proposed test change\nThe test expects 3.\n\n## Next\nx\n"
    assert proposed_test_change(text) == "The test expects 3."


def test_the_heading_is_found_at_any_level_and_case():
    assert proposed_test_change("### proposed TEST change\nwhy\n") == "why"


def test_an_empty_proposal_still_counts():
    assert proposed_test_change("## Proposed test change\n\n## Next\n") == "(no details given)"


def test_the_proposal_runs_to_the_end_of_the_file():
    assert (
        proposed_test_change("## Proposed test change\nline one\nline two") == "line one\nline two"
    )


def test_the_words_inside_other_text_are_not_a_proposal():
    assert proposed_test_change("I will not write a proposed test change.\n") is None


def test_a_red_check_with_no_files_is_never_ok(tmp_path):
    assert RedCheck((), tmp_path / "red.json").ok is False


def test_a_path_that_leaves_the_worktree_is_refused_even_when_the_file_exists(tmp_path, repo):
    (tmp_path / "secret.sh").write_text("x")
    with pytest.raises(CommandError, match="is outside the worktree"):
        lock_tests(repo, ["../secret.sh"], tmp_path / "lock")


def test_a_tampered_lock_cannot_reach_outside_the_worktree(tmp_path, repo):
    lock = locked(tmp_path, repo)
    evil = Lock(lock.folder, {"../secret.sh": "0" * 64})
    with pytest.raises(CommandError, match="is outside the worktree"):
        check_locked(repo, evil)


def test_restoring_a_violation_that_is_not_in_the_lock_does_nothing(tmp_path, repo):
    lock = locked(tmp_path, repo)
    restore_locked(repo, lock, [Violation("tests/zzz.sh", "deleted")])
    assert not (repo / "tests" / "zzz.sh").exists()
