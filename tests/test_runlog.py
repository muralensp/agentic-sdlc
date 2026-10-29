"""Run logs, measured usage and `adlc loop-status` (ADLC-018)."""

import json

import pytest

from adlc.cli import main
from adlc.commands import CommandError
from adlc.fake_runner import ok, rate_limited
from adlc.loop import Stop
from adlc.runlog import load_record, loop_status, new_run, render_status, render_summary
from adlc.runner import Usage
from test_loop import CHECK_STEPS, FIX_A, FIX_B, edit, g, make_scenario, write


@pytest.fixture
def sc(tmp_path):
    return make_scenario(tmp_path)


def logged(sc, script, **kwargs):
    return sc.run(script, key="ADLC-016", **kwargs)


def with_usage(step, usage):
    def wrapped(request):
        return ok(step(request).text, usage=usage)

    return wrapped


# --- run folders --------------------------------------------------------------------------------


def test_each_run_gets_the_next_number(tmp_path):
    first, folder1 = new_run(tmp_path, "T-001")
    second, folder2 = new_run(tmp_path, "T-001")
    other, folder3 = new_run(tmp_path, "T-002")
    assert (first, second, other) == (1, 2, 1)
    assert folder1 == tmp_path / ".adlc" / "logs" / "T-001" / "run-1" and folder2.is_dir()
    assert folder3.parent.name == "T-002"


def test_numbers_continue_after_the_highest_even_with_gaps_and_strays(tmp_path):
    logs = tmp_path / ".adlc" / "logs" / "T-001"
    (logs / "run-4").mkdir(parents=True)
    (logs / "notes").mkdir()
    (logs / "run-x").mkdir()
    assert new_run(tmp_path, "T-001")[0] == 5


# --- a real loop run leaves a complete folder ---------------------------------------------------


def test_a_run_leaves_prompts_responses_diffs_commands_and_a_summary(sc):
    outcome, _ = logged(sc, [edit(FIX_A, text="made a"), edit(FIX_B, text="made b")])
    run = sc.run_dir
    assert "BRIEF: make the tests pass" in (run / "prompts" / "iteration-1.md").read_text()
    assert "made a" in (run / "responses" / "iteration-1.md").read_text()
    assert "src/a.txt" in (run / "diffs" / "iteration-1.diff").read_text()
    assert "src/b.txt" in (run / "diffs" / "iteration-2.diff").read_text()
    for n in (0, 1, 2):  # every command with its exit code and output
        record = json.loads((run / f"verify-{n}" / "dod.json").read_text())
        assert {c["name"] for c in record["commands"]} == {"lint", "test"}
        assert all("exit_code" in c and c["out"] for c in record["commands"])
    summary = (run / "summary.md").read_text()
    assert "ADLC-016" in summary and "success" in summary
    assert outcome.record == run / "loop.json"


def test_the_record_holds_measured_usage_and_time_not_what_the_agent_said(sc):
    lie = "All done in 0.1 seconds using 1 token, it cost nothing"
    usage = Usage(100, 50, 30, 20, 0.25)

    def slow(request):
        sc.clock.t += 90  # the clock measures 90 seconds whatever the agent claims
        write(request.cwd, "src/a.txt", "a")
        write(request.cwd, "src/b.txt", "b")
        return ok(lie, usage=usage, duration=42.0, primary_model="the-model")

    outcome, _ = logged(sc, [slow])
    data = load_record(sc.run_dir)
    first = data["iterations"][0]
    assert first["duration"] == 42.0 and first["model"] == "the-model"
    assert (first["input_tokens"], first["output_tokens"]) == (100, 50)
    assert (first["cache_read_tokens"], first["cache_creation_tokens"]) == (30, 20)
    assert first["cost_usd"] == 0.25
    assert data["usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 30,
        "cache_creation_tokens": 20,
        "cost_usd_list": 0.25,
    }
    assert data["elapsed_seconds"] == 90.0  # from the clock, not from the text
    assert outcome.success


def test_usage_is_summed_over_iterations(sc):
    one, two = Usage(10, 5, 0, 0, 0.1), Usage(20, 7, 0, 0, 0.2)
    outcome, _ = logged(sc, [with_usage(edit(FIX_A), one), with_usage(edit(FIX_B), two)])
    usage = load_record(sc.run_dir)["usage"]
    assert (usage["input_tokens"], usage["output_tokens"]) == (30, 12)
    assert usage["cost_usd_list"] == pytest.approx(0.3) and outcome.success


def test_a_reverted_iteration_still_keeps_its_diff_for_the_log(sc):
    outcome, _ = logged(sc, [edit(("src/bad.txt", "oops"))])
    assert outcome.stop is Stop.REGRESSION
    assert "src/bad.txt" in (sc.run_dir / "diffs" / "iteration-1.diff").read_text()
    assert not (sc.worktree / "src" / "bad.txt").exists()
    assert g(sc.worktree, "status", "--porcelain") == ""


def test_the_scrubber_is_applied_to_everything_written_from_text(sc):
    def scrub(text):
        return text.replace("BRIEF", "[HIDDEN]").replace("made a", "[HIDDEN]")

    logged(sc, [edit(FIX_A, text="made a"), edit(FIX_B)], scrub=scrub)
    for rel in ("prompts/iteration-1.md", "responses/iteration-1.md", "summary.md"):
        text = (sc.run_dir / rel).read_text()
        assert "BRIEF" not in text and "made a" not in text
    assert "[HIDDEN]" in (sc.run_dir / "prompts" / "iteration-1.md").read_text()


def test_the_methods_used_are_recorded(sc):
    methods = {"implement": {"skill": "tdd", "source": "fallback", "sha256": "abc"}}
    logged(sc, [edit(FIX_A, FIX_B)], methods=methods)
    assert load_record(sc.run_dir)["methods"] == methods
    assert "tdd" in (sc.run_dir / "summary.md").read_text()


def test_a_paused_run_is_logged_with_its_reset_time(sc):
    outcome, _ = logged(sc, [rate_limited(resets_at=1_900_000_000)])
    data = load_record(sc.run_dir)
    assert data["stop"] == "paused" and data["resets_at"].startswith("2030")
    assert outcome.iterations == ()


def test_a_run_with_a_failing_check_records_its_last_signature(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    logged(sc, [edit(("src/n.txt", "a")), edit(("src/n.txt", "a"), ("src/x", "1"))])
    data = load_record(sc.run_dir)
    assert data["stop"] == "stuck" and "step a" in data["last_signature"]
    assert "step a" in (sc.run_dir / "summary.md").read_text()


def test_the_summary_is_what_adlc_log_prints(sc):
    logged(sc, [edit(FIX_A, FIX_B)])
    assert (sc.run_dir / "summary.md").read_text().startswith("# ADLC-016")


# --- rendering ----------------------------------------------------------------------------------

RECORD = {
    "schema": 1,
    "key": "T-001",
    "stop": None,
    "detail": "",
    "review_rounds": 1,
    "findings": "",
    "diagnosis": "",
    "elapsed_seconds": 125.5,
    "last_signature": "test: FAIL: b missing",
    "usage": {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_tokens": 5,
        "cache_creation_tokens": 6,
        "cost_usd_list": 0.5,
    },
    "methods": {},
    "iterations": [
        {"number": 1, "kind": "fix", "status": "ok", "duration": 12.0, "reverted": False,
         "input_tokens": 700, "output_tokens": 200, "changed": ["src/a.txt"], "failing": ["test"]},
        {"number": 2, "kind": "diagnosis", "status": "ok", "duration": 8.0, "reverted": True,
         "input_tokens": 500, "output_tokens": 140, "changed": [], "failing": []},
    ],
}  # fmt: skip


def test_status_shows_iterations_usage_signature_and_that_it_is_running():
    text = render_status(RECORD)
    assert "T-001" in text and "running" in text
    assert "iterations: 2" in text and "1 fix" in text and "1 diagnosis" in text
    assert "1,200" in text and "340" in text
    assert "test: FAIL: b missing" in text and "125" in text


def test_status_shows_the_stop_reason_when_the_run_has_ended():
    text = render_status({**RECORD, "stop": "stuck", "detail": "the same failure twice"})
    assert "stuck" in text and "the same failure twice" in text and "running" not in text


def test_the_summary_lists_each_iteration_and_the_stop_reason():
    text = render_summary({**RECORD, "stop": "max_iterations", "detail": "5 iterations used"})
    assert text.startswith("# T-001")
    assert "max_iterations" in text and "5 iterations used" in text
    assert "| 1 |" in text and "| 2 |" in text and "src/a.txt" in text


def test_the_summary_includes_the_diagnosis_and_findings_when_there_are_any():
    text = render_summary({**RECORD, "diagnosis": "stale cache", "findings": "rename a"})
    assert "stale cache" in text and "rename a" in text


# --- the command --------------------------------------------------------------------------------


def make_logged_repo(tmp_path, runs=2, stop="stuck"):
    repo = tmp_path / "repo"
    for n in range(1, runs + 1):
        folder = repo / ".adlc" / "logs" / "T-001" / f"run-{n}"
        folder.mkdir(parents=True)
        (folder / "loop.json").write_text(json.dumps({**RECORD, "stop": stop, "detail": f"r{n}"}))
    return repo


def run_cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_loop_status_shows_the_newest_run(tmp_path, capsys):
    repo = make_logged_repo(tmp_path)
    code, out, _ = run_cli(capsys, "loop-status", "T-001", "--repo", str(repo))
    assert code == 0 and "run 2" in out and "r2" in out


def test_loop_status_can_show_an_older_run(tmp_path, capsys):
    repo = make_logged_repo(tmp_path)
    code, out, _ = run_cli(capsys, "loop-status", "T-001", "--run", "1", "--repo", str(repo))
    assert code == 0 and "run 1" in out and "r1" in out


def test_loop_status_with_no_runs_says_so(tmp_path, capsys):
    (tmp_path / "repo").mkdir()
    code, out, _ = run_cli(capsys, "loop-status", "T-009", "--repo", str(tmp_path / "repo"))
    assert code == 0 and "no runs yet" in out


def test_loop_status_for_a_missing_run_is_an_error(tmp_path, capsys):
    repo = make_logged_repo(tmp_path, runs=1)
    code, _, err = run_cli(capsys, "loop-status", "T-001", "--run", "7", "--repo", str(repo))
    assert code == 1 and "no run 7" in err


def test_loop_status_for_a_damaged_record_is_an_error(tmp_path):
    repo = make_logged_repo(tmp_path, runs=1)
    (repo / ".adlc" / "logs" / "T-001" / "run-1" / "loop.json").write_text("{not json")
    with pytest.raises(CommandError, match="damaged"):
        loop_status(repo, "T-001")
