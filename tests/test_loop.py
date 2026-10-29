"""The Ralph loop controller (ADLC-016): one scripted scenario per stop condition."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from adlc.commands import CommandError
from adlc.config import load_config
from adlc.dod import find_bash
from adlc.fake_runner import FakeRunner, error, ok, rate_limited
from adlc.gitwork import commit, create_worktree
from adlc.loop import Review, Stop, failure_signature, run_loop
from adlc.runner import Role
from adlc.tdd import lock_tests

BASH = find_bash()
PROMISE = "<promise>COMPLETE</promise>"

CHECK = """\
[ -f src/a.txt ] || { echo "FAIL: a missing"; exit 1; }
[ -f src/b.txt ] || { echo "FAIL: b missing"; exit 1; }
echo ok
"""
# Fails with a message that changes only when the Coder writes a different letter to src/n.txt
CHECK_STEPS = 'echo "FAIL: step $(cat src/n.txt 2>/dev/null)"; exit 1\n'
LINT = '[ ! -f src/bad.txt ] || { echo "FAIL: lint found bad.txt"; exit 1; }'


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@dataclass
class Scenario:
    worktree: Path
    base: str
    settings: object
    lock: object
    run_dir: Path
    clock: Clock

    def run(self, script, review=None, **kwargs):
        runner = FakeRunner(script)
        outcome = run_loop(
            worktree=self.worktree,
            base=self.base,
            settings=self.settings,
            runner=runner,
            review=review or approve_all,
            lock=self.lock,
            prompt="BRIEF: make the tests pass",
            run_dir=self.run_dir,
            bash=BASH,
            dod_timeout=60,
            clock=self.clock,
            **kwargs,
        )
        return outcome, runner


def approve_all(worktree, base):
    return Review("approve")


def reviews(*answers):
    queue = list(answers)

    def review(worktree, base):
        return queue.pop(0)

    return review


def make_scenario(tmp_path, *, check=CHECK, limits=None, protected_extra=()):
    primary = tmp_path / "primary"
    (primary / ".adlc").mkdir(parents=True)
    own = {
        "dod": {"lint": LINT, "test": "bash tests/check.sh"},
        "tests": {"paths": ["tests/**"], "red": "bash {file}", "failure_markers": ["FAIL"]},
        "limits": limits or {},
        "protected_extra": list(protected_extra),
    }
    (primary / ".adlc" / "config.yaml").write_text(yaml.safe_dump(own, sort_keys=False))
    settings = load_config(primary)
    g(primary.parent, "init", "-q", "-b", "main", str(primary))
    write(primary, "src/app.py", "x = 1\n")
    write(primary, ".gitignore", ".adlc/logs/\n")
    g(primary, "add", "-A")
    g(primary, "commit", "-q", "-m", "init")
    made = create_worktree(
        primary,
        "ADLC-016",
        "feature",
        "ralph loop",
        settings.git,
        root=tmp_path / "wt",
        fetch=False,
    )
    work = made.path
    base = g(work, "rev-parse", "HEAD")
    write(work, "tests/check.sh", check)  # what the Tester would have written
    commit(work, "test(loop): failing tests for the loop", settings.git)
    lock = lock_tests(work, ["tests/check.sh"], tmp_path / "lock")
    return Scenario(work, base, settings, lock, tmp_path / "run", Clock())


@pytest.fixture
def sc(tmp_path):
    return make_scenario(tmp_path)


def edit(*files, text="made a change", progress=None, advance=0.0, clock=None):
    """A Coder iteration that writes files (and, optionally, progress.md)."""

    def step(request):
        for rel, content in files:
            write(request.cwd, rel, content)
        if progress is not None:
            write(request.cwd, "progress.md", progress)
        if clock is not None:
            clock.t += advance
        return ok(text)

    return step


FIX_A = ("src/a.txt", "a")
FIX_B = ("src/b.txt", "b")


# --- success ------------------------------------------------------------------------------------


def test_the_loop_succeeds_when_the_dod_passes_and_the_reviewer_approves(sc):
    outcome, runner = sc.run([edit(FIX_A), edit(FIX_B)])
    assert outcome.stop is Stop.SUCCESS and outcome.success and not outcome.escalate
    assert len(outcome.iterations) == 2 and len(runner.requests) == 2


def test_each_iteration_runs_the_coder_in_the_worktree_and_is_committed(sc):
    sc.run([edit(FIX_A), edit(FIX_B)])
    subjects = g(sc.worktree, "log", "--format=%s", f"{sc.base}..HEAD").splitlines()
    assert "chore(loop): iteration 2" in subjects and "chore(loop): iteration 1" in subjects
    assert g(sc.worktree, "status", "--porcelain") == ""


def test_a_false_completion_claim_is_logged_and_the_loop_continues(sc):
    outcome, _ = sc.run([edit(FIX_A, text=f"all done {PROMISE}"), edit(FIX_B)])
    first, second = outcome.iterations
    assert first.promise and first.false_claim and not second.false_claim
    assert outcome.stop is Stop.SUCCESS


def test_a_green_dod_is_not_success_without_the_reviewers_approval(sc):
    answers = reviews(Review("changes_requested", "rename a"), Review("approve"))
    outcome, runner = sc.run([edit(FIX_A, FIX_B), edit(("src/c.txt", "c"))], review=answers)
    assert outcome.stop is Stop.SUCCESS and outcome.review_rounds == 1
    assert "rename a" in runner.requests[1].prompt


def test_the_loop_stops_after_max_review_rounds(tmp_path):
    sc = make_scenario(tmp_path, limits={"max_review_rounds": 1})
    ask = Review("changes_requested", "still wrong")
    outcome, _ = sc.run([edit(FIX_A, FIX_B), edit(("src/c.txt", "c"))], review=reviews(ask, ask))
    assert outcome.stop is Stop.REVIEW_ROUNDS and outcome.escalate
    assert "still wrong" in outcome.findings


def test_a_reviewer_escalation_stops_the_loop(sc):
    outcome, _ = sc.run([edit(FIX_A, FIX_B)], review=reviews(Review("escalate", "needs a human")))
    assert outcome.stop is Stop.REVIEW_ESCALATED and "needs a human" in outcome.findings


def test_an_unknown_review_decision_is_refused(sc):
    with pytest.raises(CommandError, match="decision"):
        sc.run([edit(FIX_A, FIX_B)], review=reviews(Review("maybe")))


# --- carried state ------------------------------------------------------------------------------


def test_every_iteration_gets_the_same_brief_and_carried_state_but_no_history(sc):
    _, runner = sc.run(
        [
            edit(FIX_A, text="FIRST-AGENT-REPLY", progress="tried creating a.txt"),
            edit(FIX_B),
        ]
    )
    first, second = runner.requests
    assert all(r.role is Role.CODER and r.cwd == sc.worktree for r in runner.requests)
    assert first.prompt.startswith("BRIEF: make the tests pass")
    assert second.prompt.startswith("BRIEF: make the tests pass")
    assert "a missing" in first.prompt  # the failure before any code
    assert "b missing" in second.prompt and "a missing" not in second.prompt.split("## Last")[1]
    assert "tried creating a.txt" in second.prompt  # progress.md
    assert "src/a.txt" in second.prompt  # the branch diff
    assert "FIRST-AGENT-REPLY" not in second.prompt  # no conversation history


def test_the_branch_diff_leaves_out_the_locked_tests(sc):
    _, runner = sc.run([edit(FIX_A), edit(FIX_B)])
    assert "tests/check.sh" not in runner.requests[1].prompt.split("## Branch diff")[1]


def test_the_coder_method_is_passed_on(sc):
    _, runner = sc.run([edit(FIX_A, FIX_B)], method="use tdd")
    assert runner.requests[0].method == "use tdd"


def test_a_long_diff_and_failure_report_are_trimmed(sc):
    big = "line of text\n" * 5000
    _, runner = sc.run(
        [edit(("src/big.txt", big), FIX_A), edit(FIX_B)], max_diff_chars=500, max_report_chars=300
    )
    carried = runner.requests[1].prompt
    assert len(carried) < len("BRIEF") + 4000 and "trimmed" in carried


# --- stop conditions ----------------------------------------------------------------------------


def test_stops_at_max_iterations(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS, limits={"max_iterations": 3})
    outcome, runner = sc.run([edit(("src/n.txt", x)) for x in "abc"])
    assert outcome.stop is Stop.MAX_ITERATIONS and outcome.escalate
    assert len(runner.requests) == 3 and len(outcome.iterations) == 3


def test_the_max_iterations_argument_overrides_the_config(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    outcome, runner = sc.run([edit(("src/n.txt", x)) for x in "ab"], max_iterations=2)
    assert outcome.stop is Stop.MAX_ITERATIONS and len(runner.requests) == 2


def test_stops_when_the_time_is_used_up(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS, limits={"max_task_minutes": 1})
    outcome, runner = sc.run([edit(("src/n.txt", "a"), advance=61, clock=sc.clock)])
    assert outcome.stop is Stop.MAX_MINUTES and outcome.escalate
    assert len(runner.requests) == 1


def test_each_agent_call_is_limited_to_the_time_that_is_left(tmp_path):
    sc = make_scenario(tmp_path, limits={"max_task_minutes": 10})
    sc.clock.t = 100.0
    _, runner = sc.run([edit(FIX_A, FIX_B)])
    assert 0 < runner.requests[0].timeout <= 600


def test_the_same_failure_twice_is_stuck(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    outcome, runner = sc.run([edit(("src/n.txt", "a")), edit(("src/n.txt", "a"), ("src/x", "1"))])
    assert outcome.stop is Stop.STUCK and outcome.escalate
    assert len(runner.requests) == 2
    assert outcome.iterations[0].signature == outcome.iterations[1].signature != ""


def test_an_iteration_that_changes_nothing_is_stuck(sc):
    outcome, _ = sc.run([edit(FIX_A), edit(progress="thinking")])
    assert outcome.stop is Stop.STUCK and "no change" in outcome.detail


def test_a_regression_is_reverted_and_stops_the_loop(sc):
    before = g(sc.worktree, "rev-parse", "HEAD")
    outcome, _ = sc.run([edit(("src/bad.txt", "oops"))])
    assert outcome.stop is Stop.REGRESSION and outcome.escalate
    assert outcome.iterations[0].reverted
    assert not (sc.worktree / "src" / "bad.txt").exists()
    assert g(sc.worktree, "rev-parse", "HEAD") == before
    assert g(sc.worktree, "status", "--porcelain") == ""


def test_fixing_one_check_while_breaking_another_is_not_a_regression(sc):
    def remove_it(request):
        (request.cwd / "src" / "bad.txt").unlink()
        return ok("removed it")

    outcome, _ = sc.run([edit(FIX_A, FIX_B, ("src/bad.txt", "oops")), remove_it])
    assert outcome.stop is Stop.SUCCESS


def test_an_edit_outside_the_allowed_paths_is_a_guardrail_stop(tmp_path):
    sc = make_scenario(tmp_path, protected_extra=["secrets/**"])
    outcome, _ = sc.run([edit(FIX_A, ("secrets/key.txt", "k"))])
    assert outcome.stop is Stop.GUARDRAIL and outcome.escalate
    assert "secrets/key.txt" in outcome.detail
    assert not (sc.worktree / "secrets").exists() and not (sc.worktree / "src/a.txt").exists()


def test_editing_a_locked_test_is_blocked_and_undone(sc):
    outcome, _ = sc.run([edit(FIX_A, FIX_B, ("tests/check.sh", "exit 0\n"))])
    assert outcome.stop is Stop.TEST_PROTECTION and outcome.escalate
    assert "tests/check.sh" in outcome.detail
    assert (sc.worktree / "tests" / "check.sh").read_text() == CHECK


def test_deleting_a_locked_test_is_blocked(sc):
    def delete(request):
        (request.cwd / "tests" / "check.sh").unlink()
        return ok()

    outcome, _ = sc.run([delete])
    assert outcome.stop is Stop.TEST_PROTECTION
    assert (sc.worktree / "tests" / "check.sh").is_file()


def test_a_proposed_test_change_escalates(sc):
    note = "tried x\n\n## Proposed test change\nthe b check is too strict\n"
    outcome, _ = sc.run([edit(FIX_A, progress=note)])
    assert outcome.stop is Stop.TEST_PROPOSAL and outcome.escalate
    assert "too strict" in outcome.detail


def test_an_agent_error_counts_as_an_iteration_and_is_undone(sc):
    def half_done(request):
        write(request.cwd, "src/a.txt", "a")
        return error("the model fell over")

    outcome, _ = sc.run([half_done, edit(FIX_A), edit(FIX_B)])
    assert outcome.stop is Stop.SUCCESS and len(outcome.iterations) == 3
    assert outcome.iterations[0].status == "error" and outcome.iterations[0].reverted


# --- pausing ------------------------------------------------------------------------------------


def test_a_usage_limit_pauses_without_counting_an_iteration(sc):
    seen = []

    def limit(request):
        write(request.cwd, "src/a.txt", "a")  # half-finished work
        return rate_limited(resets_at=1_900_000_000)

    outcome, _ = sc.run([limit], on_pause=seen.append)
    assert outcome.stop is Stop.PAUSED and not outcome.escalate and not outcome.success
    assert outcome.iterations == () and outcome.resets_at is not None
    assert len(seen) == 1 and seen[0].status == "rate_limited"
    assert g(sc.worktree, "status", "--porcelain") == ""


# --- the record ---------------------------------------------------------------------------------


def test_the_loop_writes_a_record_for_loop_status(sc):
    outcome, _ = sc.run([edit(FIX_A, text=f"x {PROMISE}"), edit(FIX_B)])
    data = json.loads(outcome.record.read_text())
    assert data["stop"] == "success" and len(data["iterations"]) == 2
    assert data["iterations"][0]["false_claim"] is True


def test_a_dirty_worktree_is_refused_before_anything_runs(sc):
    write(sc.worktree, "src/stray.txt", "x")
    with pytest.raises(CommandError, match="uncommitted"):
        sc.run([edit(FIX_A)])


# --- failure signatures -------------------------------------------------------------------------


def test_a_signature_is_the_failing_command_and_its_first_error_line():
    a = failure_signature(
        {"test": "collected 3\nFAILED tests/t.py::test_x - boom\nFAILED b"}, ["FAIL"]
    )
    assert a == failure_signature(
        {"test": "collected 3\nFAILED tests/t.py::test_x - boom"}, ["FAIL"]
    )
    assert "test" in a and "test_x" in a


def test_numbers_do_not_change_a_signature():
    one = failure_signature({"test": "FAIL: took 0.12s at line 40"}, ["FAIL"])
    two = failure_signature({"test": "FAIL: took 0.98s at line 41"}, ["FAIL"])
    assert one == two


def test_without_a_marker_the_first_non_blank_line_is_used():
    assert "boom" in failure_signature({"lint": "\n\nboom here\nmore"}, ["FAIL"])


def test_no_failures_means_no_signature():
    assert failure_signature({}, ["FAIL"]) == ""


# --- the diagnosis iteration (ADLC-017) ---------------------------------------------------------

DIAG = "DIAGNOSIS: the cause is a stale cache in src/n.txt"


def diagnose(text=DIAG):
    """A diagnosis iteration: it is asked to look, but it also (wrongly) edits code."""

    def step(request):
        write(request.cwd, "src/probe.txt", "instrumentation")
        return ok(text)

    return step


STUCK_TWICE = [edit(("src/n.txt", "a")), edit(("src/n.txt", "a"), ("src/x", "1"))]


def test_a_stuck_loop_gets_one_diagnosis_and_the_report_reaches_the_outcome(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    script = [*STUCK_TWICE, diagnose(), edit(("src/n.txt", "a"), ("src/y", "1"))]
    outcome, runner = sc.run(script, diagnose_method="use diagnosing-bugs")
    assert outcome.stop is Stop.STUCK and outcome.escalate
    assert "stale cache" in outcome.diagnosis
    assert len(runner.requests) == 4  # two fixes, one diagnosis, one more fix: never a second one
    assert [i.kind for i in outcome.iterations] == ["fix", "fix", "diagnosis", "fix"]
    assert json.loads(outcome.record.read_text())["diagnosis"] == outcome.diagnosis


def test_the_diagnosis_call_uses_the_diagnosis_method_and_asks_for_no_fix(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    _, runner = sc.run(
        [*STUCK_TWICE, diagnose(), edit(("src/n.txt", "a"), ("src/y", "1"))],
        diagnose_method="use diagnosing-bugs",
    )
    fix, diagnosis = runner.requests[1], runner.requests[2]
    assert fix.method is None and diagnosis.method == "use diagnosing-bugs"
    assert diagnosis.role is Role.CODER and diagnosis.cwd == sc.worktree
    assert "stuck" in diagnosis.prompt and "Do not fix" in diagnosis.prompt
    assert diagnosis.prompt.startswith("BRIEF: make the tests pass")


def test_the_diagnosis_report_is_carried_into_the_next_iteration(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    _, runner = sc.run(
        [*STUCK_TWICE, diagnose(), edit(("src/n.txt", "a"), ("src/y", "1"))], diagnose_method="m"
    )
    assert "stale cache" not in runner.requests[1].prompt
    assert "stale cache" in runner.requests[3].prompt


def test_a_diagnosis_that_unblocks_the_loop_lets_it_finish(sc):
    script = [edit(FIX_A), edit(FIX_A, ("src/x", "1")), diagnose(), edit(FIX_B)]
    outcome, runner = sc.run(script, diagnose_method="m")
    assert outcome.stop is Stop.SUCCESS and "stale cache" in outcome.diagnosis
    assert len(runner.requests) == 4


def test_what_the_diagnosis_edits_is_thrown_away(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    sc.run(
        [*STUCK_TWICE, diagnose(), edit(("src/n.txt", "a"), ("src/y", "1"))], diagnose_method="m"
    )
    assert not (sc.worktree / "src" / "probe.txt").exists()
    assert g(sc.worktree, "status", "--porcelain") == ""


def test_the_diagnosis_does_not_use_up_an_iteration(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS, limits={"max_iterations": 3})
    script = [*STUCK_TWICE, diagnose(), edit(("src/n.txt", "b"))]
    outcome, runner = sc.run(script, diagnose_method="m")
    assert outcome.stop is Stop.MAX_ITERATIONS and len(runner.requests) == 4
    assert sum(i.kind == "fix" for i in outcome.iterations) == 3


def test_a_no_change_stuck_loop_is_diagnosed_once_and_keeps_its_notes(sc):
    script = [edit(FIX_A), edit(progress="my notes"), diagnose(), edit(progress="more")]
    outcome, runner = sc.run(script, diagnose_method="m")
    assert outcome.stop is Stop.STUCK and "no change" in outcome.detail
    assert len(runner.requests) == 4 and "stale cache" in outcome.diagnosis
    assert "my notes" in runner.requests[2].prompt  # progress.md survived the diagnosis


def test_without_a_diagnosis_method_a_stuck_loop_escalates_at_once(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    outcome, runner = sc.run(STUCK_TWICE)
    assert outcome.stop is Stop.STUCK and outcome.diagnosis == ""
    assert len(runner.requests) == 2


def test_a_failed_diagnosis_still_counts_as_the_one_and_the_loop_escalates(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    outcome, runner = sc.run([*STUCK_TWICE, error("fell over")], diagnose_method="m")
    assert outcome.stop is Stop.STUCK and "diagnosis" in outcome.detail
    assert outcome.diagnosis == "" and len(runner.requests) == 3


def test_a_usage_limit_during_diagnosis_pauses_the_loop(tmp_path):
    sc = make_scenario(tmp_path, check=CHECK_STEPS)
    outcome, _ = sc.run([*STUCK_TWICE, rate_limited(resets_at=1_900_000_000)], diagnose_method="m")
    assert outcome.stop is Stop.PAUSED and not outcome.escalate
    assert g(sc.worktree, "status", "--porcelain") == ""


def test_the_reviewer_is_not_asked_again_about_code_that_did_not_change(sc):
    asked = []

    def review(worktree, base):
        asked.append(1)
        return Review("changes_requested", "tidy up") if len(asked) == 1 else Review("approve")

    script = [
        edit(FIX_A, FIX_B),  # green: the Reviewer asks for changes
        edit(progress="no idea what to change"),  # no change: stuck, so diagnose
        diagnose(),
        edit(("src/c.txt", "c")),
    ]
    outcome, runner = sc.run(script, review=review, diagnose_method="m")
    assert outcome.stop is Stop.SUCCESS and len(runner.requests) == 4
    assert len(asked) == 2  # once before the stuck iteration, once after the real change
