"""Verifying a task's worktree: run the Definition of Done and record the evidence (ADLC-014)."""

import json
from pathlib import Path

import pytest
import yaml

from adlc.baseline import SCHEMA, Baseline
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.dod import CommandResult, find_bash
from adlc.verify import failure_report, verify

BASH = find_bash()


def settings_for(tmp_path: Path, dod: dict):
    """Settings as loaded from the primary checkout."""
    primary = tmp_path / "primary"
    (primary / ".adlc").mkdir(parents=True, exist_ok=True)
    (primary / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": dod}, sort_keys=False))
    return load_config(primary)


def make_worktree(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return work


def result(name, status="pass", code=0, **kw) -> CommandResult:
    fields = dict(
        name=name,
        command="x",
        status=status,
        exit_code=code,
        duration=0.1,
        attempts=1,
        flaky=False,
        out=f"{name}.out",
        err=f"{name}.err",
    )
    fields.update(kw)
    return CommandResult(**fields)


def baseline_of(*results: CommandResult, commit="abc123") -> Baseline:
    return Baseline(SCHEMA, "2026-10-29T10:00:00+00:00", commit, False, "win32", tuple(results))


def run(tmp_path, dod, baseline=None, **kwargs):
    settings = settings_for(tmp_path, dod)
    work = make_worktree(tmp_path)
    return verify(
        work, settings, tmp_path / "out", baseline=baseline, bash=BASH, timeout=30, **kwargs
    )


GOOD = {"lint": "echo linted", "test": "echo tested"}


# --- the basic run ------------------------------------------------------------------------------


def test_a_clean_run_is_ready(tmp_path):
    v = run(tmp_path, GOOD)
    assert (v.complete, v.green, v.blocked, v.ready) == (True, True, False, True)
    assert [r.name for r in v.results] == ["lint", "test"]
    assert [x.verdict for x in v.verdicts] == ["pass", "pass"]


def test_each_command_is_saved_with_its_exit_code_and_output(tmp_path):
    v = run(tmp_path, {"lint": "echo to-out; echo to-err >&2", "test": "exit 5"})
    out = tmp_path / "out"
    lint, test = v.results
    assert (out / lint.out).read_bytes().decode().strip() == "to-out"
    assert (out / lint.err).read_bytes().decode().strip() == "to-err"
    assert test.exit_code == 5 and test.status == "fail"


def test_a_command_that_prints_ok_but_exits_non_zero_is_a_failure(tmp_path):
    v = run(tmp_path, {"lint": "echo OK; echo ALL PASSED; exit 1", "test": "echo fine"})
    assert v.results[0].status == "fail"
    assert (v.green, v.blocked, v.ready) == (False, True, False)


def test_noisy_output_with_exit_zero_passes(tmp_path):
    v = run(tmp_path, {"lint": "echo error: nope; echo failed >&2", "test": "echo x"})
    assert v.green and v.ready


def test_commands_run_in_the_worktree(tmp_path):
    work = make_worktree(tmp_path)
    (work / "marker.txt").write_text("x")
    v = run(tmp_path, {"lint": "test -f marker.txt", "test": "test -f marker.txt"})
    assert v.green


def test_a_relative_bash_script_from_the_worktree_runs(tmp_path):
    work = make_worktree(tmp_path)
    scripts = work / "build" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "lint.sh").write_bytes(b"#!/usr/bin/env bash\nset -e\necho from-script\nexit 0\n")
    (scripts / "test.sh").write_bytes(b"#!/usr/bin/env bash\necho nope >&2\nexit 9\n")
    v = run(tmp_path, {"lint": "bash build/scripts/lint.sh", "test": "bash build/scripts/test.sh"})
    assert [(r.status, r.exit_code) for r in v.results] == [("pass", 0), ("fail", 9)]
    assert (tmp_path / "out" / v.results[0].out).read_bytes().decode().strip() == "from-script"


def test_the_commands_come_from_the_given_settings_not_from_the_worktree(tmp_path):
    work = make_worktree(tmp_path)
    (work / ".adlc").mkdir()
    (work / ".adlc" / "config.yaml").write_text(
        yaml.safe_dump({"dod": {"lint": "exit 0", "test": "exit 0"}})
    )
    v = run(tmp_path, {"lint": "exit 0", "test": "exit 1"})
    assert v.results[1].status == "fail" and not v.green


def test_a_hung_command_times_out_and_blocks(tmp_path):
    settings = settings_for(tmp_path, {"lint": "echo ok", "test": "sleep 60"})
    v = verify(make_worktree(tmp_path), settings, tmp_path / "out", bash=BASH, timeout=0.5)
    assert v.results[1].status == "timeout"
    assert (v.green, v.blocked) == (False, True)


def test_a_flaky_pass_counts_as_a_pass_and_is_listed(tmp_path):
    command = "if test -f seen; then exit 0; fi; touch seen; exit 1"
    v = run(tmp_path, {"lint": "echo ok", "test": command})
    assert v.ready and v.green
    assert v.flaky == ("test",)


def test_progress_is_reported(tmp_path):
    seen = []
    run(tmp_path, GOOD, progress=seen.append)
    assert seen == ["lint", "test"]


# --- the baseline -------------------------------------------------------------------------------


def test_a_failure_that_was_already_there_does_not_block(tmp_path):
    base = baseline_of(result("lint"), result("test", "fail", 1))
    v = run(tmp_path, {"lint": "echo ok", "test": "exit 1"}, baseline=base)
    assert [x.verdict for x in v.verdicts] == ["pass", "pre-existing"]
    assert (v.green, v.blocked, v.ready) == (False, False, True)
    assert v.pre_existing == ("test",)


def test_a_new_failure_blocks(tmp_path):
    base = baseline_of(result("lint"), result("test"))
    v = run(tmp_path, {"lint": "exit 2", "test": "echo ok"}, baseline=base)
    assert [x.verdict for x in v.verdicts] == ["new", "pass"]
    assert v.blocked and not v.ready


def test_a_fixed_command_is_reported_fixed(tmp_path):
    base = baseline_of(result("lint", "fail", 1), result("test"))
    v = run(tmp_path, GOOD, baseline=base)
    assert v.verdicts[0].verdict == "fixed" and v.ready


def test_with_no_baseline_every_failure_is_new(tmp_path):
    v = run(tmp_path, {"lint": "exit 1", "test": "echo ok"}, baseline=None)
    assert v.verdicts[0].verdict == "new" and v.blocked


# --- complete, green and the quick run ----------------------------------------------------------


def test_a_partial_run_is_never_ready(tmp_path):
    v = run(tmp_path, {**GOOD, "build": "echo built"}, only=["test"])
    assert [r.status for r in v.results] == ["skipped", "pass", "skipped"]  # dod order
    assert (v.complete, v.green, v.blocked, v.ready) == (False, True, False, False)
    assert set(v.skipped) == {"lint", "build"}


def test_a_command_the_baseline_skipped_is_not_run_and_does_not_stop_ready(tmp_path):
    base = baseline_of(result("lint"), result("test"), result("smoke", "skipped", None))
    v = run(tmp_path, {**GOOD, "smoke": "exit 1"}, baseline=base)
    assert [r.status for r in v.results] == ["pass", "pass", "skipped"]
    assert v.not_run == ("smoke",)
    assert v.skipped == ()
    assert (v.complete, v.blocked, v.ready) == (True, False, True)


def test_naming_a_baseline_skipped_command_runs_it(tmp_path):
    base = baseline_of(result("lint"), result("test"), result("smoke", "skipped", None))
    v = run(tmp_path, {**GOOD, "smoke": "exit 1"}, baseline=base, only=["smoke"])
    assert [r.status for r in v.results] == ["skipped", "skipped", "fail"]
    assert v.not_run == ()
    assert v.blocked


def test_without_a_baseline_every_command_runs(tmp_path):
    v = run(tmp_path, {**GOOD, "smoke": "exit 1"})
    assert v.results[2].status == "fail" and v.not_run == ()


def test_the_record_and_report_list_commands_not_run(tmp_path):
    base = baseline_of(result("lint"), result("test"), result("smoke", "skipped", None))
    v = run(tmp_path, {**GOOD, "smoke": "exit 1"}, baseline=base)
    data = record(tmp_path)
    assert data["not_run"] == ["smoke"] and data["ready"] is True
    assert "Not run (skipped in baseline): smoke" in failure_report(v, tmp_path / "out")


def test_a_failing_quick_run_is_blocked(tmp_path):
    v = run(tmp_path, {"lint": "echo ok", "test": "exit 1"}, only=["test"])
    assert v.blocked and not v.ready


def test_required_commands_must_exist_even_for_a_quick_run(tmp_path):
    settings = settings_for(tmp_path, {"lint": "echo ok"})
    assert "test" not in settings.dod
    with pytest.raises(CommandError, match="test"):
        verify(make_worktree(tmp_path), settings, tmp_path / "out", bash=BASH, only=["lint"])


def test_unknown_names_are_refused(tmp_path):
    with pytest.raises(CommandError, match="nope"):
        run(tmp_path, GOOD, only=["nope"])


# --- the record ---------------------------------------------------------------------------------


def record(tmp_path) -> dict:
    return json.loads((tmp_path / "out" / "dod.json").read_bytes().decode("utf-8"))


def test_the_record_is_written_next_to_the_output(tmp_path):
    base = baseline_of(result("lint"), result("test", "fail", 1), commit="deadbeef")
    v = run(tmp_path, {"lint": "echo ok", "test": "echo 'a b' >&2; exit 1"}, baseline=base)
    data = record(tmp_path)
    assert v.record == tmp_path / "out" / "dod.json"
    assert data["schema"] == 1
    assert (data["complete"], data["green"], data["blocked"], data["ready"]) == (
        True,
        False,
        False,
        True,
    )
    assert data["baseline"] == {"commit": "deadbeef", "taken_at": "2026-10-29T10:00:00+00:00"}
    assert [c["name"] for c in data["commands"]] == ["lint", "test"]
    test = data["commands"][1]
    assert (test["status"], test["verdict"], test["baseline_status"], test["exit_code"]) == (
        "fail",
        "pre-existing",
        "fail",
        1,
    )
    assert test["attempts"] == 2 and test["command"].startswith("echo 'a b'")
    assert (tmp_path / "out" / test["err"]).is_file()
    assert data["duration"] >= 0 and data["finished_at"] and data["worktree"]


def test_the_record_without_a_baseline_says_so(tmp_path):
    run(tmp_path, GOOD)
    data = record(tmp_path)
    assert data["baseline"] is None
    assert all(c["baseline_status"] is None for c in data["commands"])


def test_the_record_lists_skipped_commands(tmp_path):
    run(tmp_path, GOOD, only=["test"])
    data = record(tmp_path)
    assert [(c["name"], c["status"], c["verdict"]) for c in data["commands"]] == [
        ("lint", "skipped", "skipped"),
        ("test", "pass", "pass"),
    ]
    assert data["complete"] is False and data["ready"] is False


# --- the failure report -------------------------------------------------------------------------


def failing(tmp_path, **kwargs):
    dod = {
        "lint": "echo lint-out; echo lint-err >&2; exit 2",
        "test": "for i in $(seq 1 400); do echo line-$i; done; echo FINAL-ERROR >&2; exit 1",
    }
    return run(tmp_path, dod, **kwargs)


def test_the_report_names_each_new_failure_with_its_exit_code_and_output(tmp_path):
    v = failing(tmp_path)
    text = failure_report(v, tmp_path / "out", max_chars=6000)
    assert "lint" in text and "exit 2" in text and "lint-err" in text and "lint-out" in text
    assert "test" in text and "exit 1" in text and "FINAL-ERROR" in text


def test_the_report_keeps_the_end_of_long_output_and_respects_the_limit(tmp_path):
    v = failing(tmp_path)
    text = failure_report(v, tmp_path / "out", max_chars=1500)
    assert len(text) <= 1500
    assert "line-400" in text and "line-1\n" not in text
    assert "FINAL-ERROR" in text


def test_the_report_points_to_the_full_output(tmp_path):
    v = failing(tmp_path)
    text = failure_report(v, tmp_path / "out", max_chars=6000)
    assert v.results[1].out in text and v.results[1].err in text


def test_pre_existing_failures_are_listed_but_not_detailed(tmp_path):
    base = baseline_of(result("lint", "fail", 2), result("test"))
    v = run(tmp_path, {"lint": "echo hidden-detail; exit 2", "test": "exit 1"}, baseline=base)
    text = failure_report(v, tmp_path / "out", max_chars=6000)
    assert "Pre-existing" in text and "lint" in text.split("Pre-existing")[1]
    assert "hidden-detail" not in text


def test_with_no_new_failures_the_report_says_so_and_lists_the_rest(tmp_path):
    command = "if test -f seen; then exit 0; fi; touch seen; exit 1"
    v = run(tmp_path, {"lint": "echo ok", "test": command}, only=["test"])
    text = failure_report(v, tmp_path / "out", max_chars=6000)
    assert "No new failures" in text
    assert "Flaky" in text and "test" in text.split("Flaky")[1]
    assert "Skipped" in text and "lint" in text.split("Skipped")[1]


def test_a_timeout_is_reported_as_one(tmp_path):
    settings = settings_for(tmp_path, {"lint": "echo ok", "test": "sleep 60"})
    v = verify(make_worktree(tmp_path), settings, tmp_path / "out", bash=BASH, timeout=0.5)
    text = failure_report(v, tmp_path / "out", max_chars=6000)
    assert "timed out" in text


def test_a_tiny_limit_still_returns_something_within_it(tmp_path):
    v = failing(tmp_path)
    text = failure_report(v, tmp_path / "out", max_chars=200)
    assert 0 < len(text) <= 200


def test_a_baseline_skipped_command_that_is_no_longer_configured_is_ignored(tmp_path):
    base = baseline_of(result("lint"), result("test"), result("old", "skipped", None))
    v = run(tmp_path, GOOD, baseline=base)
    assert v.not_run == () and v.ready
