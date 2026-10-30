"""The Reviewer: two-axis verdict, gate checklists, and the call that produces them (ADLC-019)."""

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from adlc.config import Models
from adlc.fake_runner import FakeRunner, error, ok
from adlc.loop import Review
from adlc.review import (
    GATES,
    ReviewReport,
    make_reviewer,
    parse_report,
    review_prompt,
    signoff,
)
from adlc.runner import Role

MODELS = Models(coder="sonnet", tester=None, reviewer="opus", pr_writer=None)


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def gate(result="Pass", evidence="checked"):
    return {"result": result, "evidence": evidence}


def reply(decision="approve", standards=(), spec=(), gates=None, wrap=True) -> str:
    body = {
        "decision": decision,
        "standards": list(standards),
        "spec": list(spec),
        "gates": gates if gates is not None else {"1": gate(), "2": gate(), "3": gate()},
    }
    text = json.dumps(body)
    return f"Looked at the diff.\n```json\n{text}\n```\n" if wrap else text


# --- parse_report -------------------------------------------------------------------------------


def test_an_approval_with_clean_gates_is_approved():
    report = parse_report(reply())
    assert report.decision == "approve"
    assert [g.number for g in report.gates] == [1, 2, 3, 4]  # 1-3 run, 4 is N/A, 5 never


def test_findings_are_listed_per_axis():
    report = parse_report(
        reply("changes_requested", standards=["naming: use snake_case"], spec=["AC 2 is missing"])
    )
    assert report.decision == "changes_requested"
    review = report.as_review()
    assert isinstance(review, Review) and review.decision == "changes_requested"
    assert "Standards" in review.findings and "naming: use snake_case" in review.findings
    assert "Spec" in review.findings and "AC 2 is missing" in review.findings


def test_a_reply_without_a_fence_is_still_read():
    assert parse_report(reply(wrap=False)).decision == "approve"


def test_the_last_json_block_wins():
    text = reply("changes_requested", spec=["x"]) + "\nOn reflection:\n" + reply("approve")
    assert parse_report(text).decision == "approve"


def test_an_approval_with_a_failed_gate_is_changes_requested():
    gates = {"1": gate(), "2": gate("Fail", "coverage is 60%"), "3": gate()}
    report = parse_report(reply("approve", gates=gates))
    assert report.decision == "changes_requested"
    assert (
        "Gate 2" in report.as_review().findings and "coverage is 60%" in report.as_review().findings
    )


def test_an_approval_with_open_findings_is_changes_requested():
    report = parse_report(reply("approve", spec=["AC 1 not implemented"]))
    assert report.decision == "changes_requested"


def test_gate_four_is_always_not_applicable_and_gate_five_is_never_run():
    gates = {"1": gate(), "2": gate(), "3": gate(), "4": gate("Fail"), "5": gate("Pass")}
    report = parse_report(reply(gates=gates))
    by_number = {g.number: g for g in report.gates}
    assert by_number[4].result == "Not Applicable"
    assert 5 not in by_number
    assert report.decision == "approve"  # the model's opinion of gate 4 or 5 cannot block


@pytest.mark.parametrize(
    "text",
    [
        "I could not decide.",  # no verdict at all
        "```json\n{not json}\n```",
        reply("maybe"),  # unknown decision
        reply(gates={"1": gate(), "2": gate()}),  # gate 3 missing
        reply(gates={"1": gate(), "2": gate("Great"), "3": gate()}),  # unknown result
    ],
)
def test_an_unusable_reply_escalates_rather_than_guessing(text):
    report = parse_report(text)
    assert report.decision == "escalate"
    assert report.as_review().findings  # says why


def test_an_escalation_keeps_the_reviewers_reasons():
    report = parse_report(reply("escalate", spec=["the brief contradicts itself"]))
    assert report.decision == "escalate"
    assert "the brief contradicts itself" in report.as_review().findings


def test_not_applicable_gate_three_is_allowed():
    gates = {"1": gate(), "2": gate(), "3": gate("Not Applicable", "no endpoint changed")}
    assert parse_report(reply(gates=gates)).decision == "approve"


def test_signoff_line_names_reviewer_date_scope_and_result():
    gates = {"1": gate(), "2": gate("Pass with noted exceptions", "one TODO"), "3": gate()}
    report = parse_report(reply(gates=gates))
    line = signoff(report.gates[1], model="opus", scope="ADLC-019", on=date(2026, 10, 30))
    assert line == (
        "Gate 2 (Backend): reviewed by opus (agent), 2026-10-30, scope ADLC-019, "
        "result Pass with noted exceptions"
    )


def test_gates_are_the_confirmed_three_plus_the_two_that_never_block():
    assert [g.number for g in GATES if g.run] == [1, 2, 3]
    assert {g.number: g.name for g in GATES} == {
        1: "Architecture",
        2: "Backend",
        3: "API",
        4: "Frontend",
        5: "Release",
    }


# --- the prompt ---------------------------------------------------------------------------------


def test_the_prompt_carries_brief_diff_and_the_gates_to_fill():
    prompt = review_prompt("BRIEF TEXT", "DIFF TEXT")
    assert "BRIEF TEXT" in prompt and "DIFF TEXT" in prompt
    assert "Standards" in prompt and "Spec" in prompt
    for n in (1, 2, 3):
        assert f"Gate {n}" in prompt
    assert "Gate 5" not in prompt  # ADLC never runs the release gate


# --- make_reviewer ------------------------------------------------------------------------------


@pytest.fixture
def worktree(tmp_path):
    g(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("one\n")
    g(tmp_path, "add", "-A")
    g(tmp_path, "commit", "-q", "-m", "init")
    base = g(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.txt").write_text("two\n")
    (tmp_path / "new.txt").write_text("fresh\n")  # untracked: must still be in the diff
    return tmp_path, base


def test_the_reviewer_is_a_read_only_call_on_the_reviewer_role(worktree):
    path, base = worktree
    runner = FakeRunner([ok(reply())], models=MODELS)
    verdict = make_reviewer(runner, brief="BRIEF", method="METHOD")(path, base)
    assert verdict.decision == "approve"
    (request,) = runner.requests
    assert request.role is Role.REVIEWER and request.cwd == path
    assert request.method == "METHOD"
    assert "BRIEF" in request.prompt and "+two" in request.prompt and "+fresh" in request.prompt
    assert not {"Edit", "Write", "Bash"} & set(request.allowed_tools)
    assert {"Edit", "Write"} <= set(request.disallowed_tools)


def test_a_failed_call_escalates_with_the_reason(worktree):
    path, base = worktree
    runner = FakeRunner([error("claude exited 1")], models=MODELS)
    verdict = make_reviewer(runner, brief="B")(path, base)
    assert verdict.decision == "escalate" and "claude exited 1" in verdict.findings


def test_the_report_is_kept_for_the_run_log(worktree):
    path, base = worktree
    reports: list[ReviewReport] = []
    runner = FakeRunner([ok(reply("changes_requested", spec=["AC 2"]))], models=MODELS)
    make_reviewer(runner, brief="B", on_report=reports.append)(path, base)
    assert [r.decision for r in reports] == ["changes_requested"]


def test_an_empty_diff_is_not_sent_to_the_model(worktree):
    path, _ = worktree
    head = g(path, "rev-parse", "HEAD")
    g(path, "add", "-A")
    g(path, "commit", "-q", "-m", "all in")
    runner = FakeRunner([], models=MODELS)
    verdict = make_reviewer(runner, brief="B")(path, g(path, "rev-parse", "HEAD"))
    assert verdict.decision == "escalate" and "no changes" in verdict.findings
    assert not runner.requests
    assert head != g(path, "rev-parse", "HEAD")
