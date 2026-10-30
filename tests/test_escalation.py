"""The escalation engine, the NEEDS_HUMAN queue and the person's four answers (ADLC-020)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from adlc.board import Board
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.escalation import (
    Escalation,
    Facts,
    Trigger,
    approve,
    edit,
    evaluate,
    from_outcome,
    list_open,
    merge_allowed,
    raise_escalation,
    regressions_from,
    reject,
    render_report,
    report,
    retry,
)
from adlc.loop import LoopOutcome, Stop
from adlc.states import IllegalTransition
from adlc.verify import Verdict
from test_commands import board_text, brief_text, make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path,
        [("T-001", "IN_PROGRESS"), ("T-002", "IN_REVIEW"), ("T-003", "BACKLOG")],
    )


@pytest.fixture
def settings(repo):
    return load_config(repo)


def triggers(found):
    return [e.trigger for e in found]


def status_of(repo, key):
    return next(r for r in Board(repo / "docs" / "WORK-ITEMS.md").rows() if r.key == key)


def cell(repo, key, name):
    return next(v for k, v in status_of(repo, key).cells.items() if k.lower() == name)


def outcome(stop, detail="why", findings="", record=Path("run-1/loop.json")):
    return LoopOutcome(stop, detail, (), 0, findings, None, record)


# --- every trigger in 8.4a has a test ----------------------------------------------------------


def test_a_clean_task_raises_nothing(settings):
    assert evaluate(Facts(changed=("src/app.py",), diff_lines=30), settings) == []


@pytest.mark.parametrize(
    ("stop", "trigger"),
    [
        (Stop.MAX_ITERATIONS, Trigger.LOOP_EXHAUSTED),
        (Stop.MAX_MINUTES, Trigger.LOOP_EXHAUSTED),
        (Stop.STUCK, Trigger.LOOP_EXHAUSTED),
        (Stop.REGRESSION, Trigger.LOOP_EXHAUSTED),
        (Stop.REVIEW_ESCALATED, Trigger.REVIEW_UNRESOLVED),
        (Stop.REVIEW_ROUNDS, Trigger.REVIEW_UNRESOLVED),
        (Stop.GUARDRAIL, Trigger.PROTECTED_AREA),
        (Stop.TEST_PROTECTION, Trigger.TEST_INTEGRITY),
        (Stop.TEST_PROPOSAL, Trigger.TEST_INTEGRITY),
    ],
)
def test_every_loop_stop_maps_to_a_trigger(stop, trigger):
    found = from_outcome(outcome(stop, "the detail", findings="fix the names"))
    assert found.trigger is trigger
    assert "the detail" in found.reason
    assert "run-1/loop.json" in found.evidence[0].replace("\\", "/")
    assert found.next_step


def test_success_and_a_usage_pause_raise_nothing():
    assert from_outcome(outcome(Stop.SUCCESS)) is None
    assert from_outcome(outcome(Stop.PAUSED)) is None


def test_review_findings_are_evidence(settings):
    found = from_outcome(outcome(Stop.REVIEW_ROUNDS, findings="names are unclear"))
    assert "names are unclear" in " ".join(found.evidence)


def test_protected_area_names_the_files(settings):
    changed = ("src/app.py", ".github/workflows/ci.yml", "db/migrations/001.sql")
    (found,) = evaluate(Facts(changed=changed), settings)
    assert found.trigger is Trigger.PROTECTED_AREA
    assert found.evidence == (".github/workflows/ci.yml", "db/migrations/001.sql")


def test_the_board_and_claude_md_are_exempt_from_protection(settings):
    protected = replace(settings, protected=(*settings.protected, "CLAUDE.md", "docs/**"))
    assert evaluate(Facts(changed=("docs/WORK-ITEMS.md", "CLAUDE.md")), protected) == []


def test_a_diff_over_the_soft_limit_escalates_on_size(settings):
    soft = settings.limits.max_diff_lines
    assert evaluate(Facts(diff_lines=soft), settings) == []
    (found,) = evaluate(Facts(diff_lines=soft + 1), settings)
    assert found.trigger is Trigger.SCOPE_SIZE
    assert str(soft) in found.reason


def test_the_hard_ceiling_counts_tests_too(settings):
    hard = settings.limits.max_diff_lines_hard
    facts = Facts(diff_lines=10, test_diff_lines=hard)
    (found,) = evaluate(facts, settings)
    assert found.trigger is Trigger.SCOPE_SIZE
    assert str(hard) in found.reason


def test_files_outside_the_task_and_new_dependencies_escalate_on_scope(settings):
    found = evaluate(
        Facts(outside_task=("src/other.py",), new_dependencies=("left-pad",)), settings
    )
    assert triggers(found) == [Trigger.SCOPE_SIZE, Trigger.SCOPE_SIZE]
    assert "src/other.py" in found[0].evidence
    assert "left-pad" in found[1].evidence


def test_a_quality_regression_names_the_gate(settings):
    (found,) = evaluate(Facts(regressions=("lint", "secret-scan")), settings)
    assert found.trigger is Trigger.QUALITY_REGRESSION
    assert found.evidence == ("lint", "secret-scan")


def test_regressions_come_from_new_failures_only():
    verdicts = (
        Verdict("lint", "new", "fail", "pass"),
        Verdict("test", "pre-existing", "fail", "fail"),
        Verdict("build", "pass", "pass", "pass"),
    )
    assert regressions_from(verdicts) == ("lint",)


def test_test_integrity(settings):
    (found,) = evaluate(Facts(test_integrity="the Coder removed test_a"), settings)
    assert found.trigger is Trigger.TEST_INTEGRITY
    assert "removed test_a" in found.reason


def test_spec_problem_and_an_undeclared_adr(settings):
    found = evaluate(Facts(spec_problem="criteria 1 and 3 contradict", needs_adr=True), settings)
    assert triggers(found) == [Trigger.SPEC_PROBLEM, Trigger.SPEC_PROBLEM]
    assert "contradict" in found[0].reason
    assert "ADR" in found[1].reason


def test_external_check(settings):
    (found,) = evaluate(Facts(external="remote CI failed after the local checks passed"), settings)
    assert found.trigger is Trigger.EXTERNAL_CHECK


def test_guardrail(settings):
    (found,) = evaluate(Facts(guardrail=("ran `curl` which AGENTS.md does not allow",)), settings)
    assert found.trigger is Trigger.GUARDRAIL
    assert "curl" in found.evidence[0]


def test_task_flag(settings):
    (found,) = evaluate(Facts(requires_approval=True), settings)
    assert found.trigger is Trigger.TASK_FLAG


def test_several_triggers_can_fire_together_in_table_order(settings):
    found = evaluate(
        Facts(requires_approval=True, changed=(".adlc/x",), regressions=("lint",)), settings
    )
    assert triggers(found) == [
        Trigger.PROTECTED_AREA,
        Trigger.QUALITY_REGRESSION,
        Trigger.TASK_FLAG,
    ]


# --- raising an escalation -----------------------------------------------------------------------


def stop_for(trigger=Trigger.LOOP_EXHAUSTED, reason="gave up", **kw):
    return Escalation(trigger, reason, kw.get("evidence", ("run-1/loop.json",)), "look at it")


def test_raising_sets_needs_human_and_writes_the_reason_to_the_notes(repo):
    raise_escalation(repo, "T-001", stop_for(), run_log="logs/T-001/run-1")
    assert cell(repo, "T-001", "status") == "NEEDS_HUMAN"
    notes = cell(repo, "T-001", "notes")
    assert "loop_exhausted" in notes
    assert "gave up" in notes
    assert "logs/T-001/run-1" in notes
    assert board_text(repo).count("\n") == board_text(repo).count("\n")  # whole file intact


def test_raising_touches_only_that_rows_two_cells(repo):
    before = board_text(repo).splitlines()
    raise_escalation(repo, "T-001", stop_for())
    after = board_text(repo).splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert len(before) == len(after)
    assert len(changed) == 1
    assert after[changed[0]].startswith("| T-001 |")


def test_the_record_holds_every_trigger_and_the_next_step(repo):
    raise_escalation(repo, "T-002", [stop_for(), stop_for(Trigger.TASK_FLAG, "needs sign-off")])
    (record,) = list_open(repo)
    assert record["key"] == "T-002"
    assert [e["trigger"] for e in record["escalations"]] == ["loop_exhausted", "task_flag"]
    assert record["escalations"][0]["next_step"] == "look at it"
    assert record["approved"] is False


def test_raising_from_backlog_is_refused_and_changes_nothing(repo):
    before = board_text(repo)
    with pytest.raises(IllegalTransition):
        raise_escalation(repo, "T-003", stop_for())
    assert board_text(repo) == before
    assert list_open(repo) == []


def test_raising_tells_the_notify_hook(repo):
    told = []
    raise_escalation(repo, "T-001", stop_for(), notify=lambda key, found: told.append((key, found)))
    assert told[0][0] == "T-001"
    assert told[0][1][0].trigger is Trigger.LOOP_EXHAUSTED


def test_raising_twice_adds_to_the_open_record(repo):
    raise_escalation(repo, "T-001", stop_for())
    raise_escalation(repo, "T-001", stop_for(Trigger.TASK_FLAG, "sign-off"))
    (record,) = list_open(repo)
    assert len(record["escalations"]) == 2


# --- no merge without approval -------------------------------------------------------------------


def test_an_escalated_task_cannot_merge_until_it_is_approved(repo):
    assert merge_allowed(repo, "T-001")  # nothing escalated
    raise_escalation(repo, "T-001", stop_for())
    assert not merge_allowed(repo, "T-001")
    approve(repo, "T-001")
    assert merge_allowed(repo, "T-001")


def test_a_later_escalation_withdraws_an_earlier_approval(repo):
    raise_escalation(repo, "T-001", stop_for())
    approve(repo, "T-001")
    raise_escalation(repo, "T-001", stop_for(Trigger.EXTERNAL_CHECK, "CI failed"))
    assert not merge_allowed(repo, "T-001")


def test_approve_keeps_the_task_waiting_for_the_merge_step(repo):
    raise_escalation(repo, "T-001", stop_for())
    approve(repo, "T-001")
    assert cell(repo, "T-001", "status") == "NEEDS_HUMAN"
    assert "approved" in cell(repo, "T-001", "notes")


# --- the person's other answers ------------------------------------------------------------------


def test_retry_goes_back_to_the_queue_with_the_note_carried(repo):
    raise_escalation(repo, "T-001", stop_for())
    retry(repo, "T-001", note="use the existing helper")
    assert cell(repo, "T-001", "status") == "BACKLOG"
    assert list_open(repo) == []
    note = repo / ".adlc" / "escalations" / "T-001.note.md"
    assert "use the existing helper" in note.read_text()
    assert merge_allowed(repo, "T-001")


def test_retry_refine_reopens_grilling_for_a_spec_problem(repo):
    raise_escalation(repo, "T-001", stop_for(Trigger.SPEC_PROBLEM, "contradiction"))
    called = []
    retry(repo, "T-001", refine=True, refine_fn=lambda r, k: called.append((r, k)))
    assert called == [(repo, "T-001")]
    assert cell(repo, "T-001", "status") == "DRAFT"


def test_retry_refine_is_refused_for_any_other_trigger(repo):
    raise_escalation(repo, "T-001", stop_for(Trigger.LOOP_EXHAUSTED))
    with pytest.raises(CommandError, match="spec"):
        retry(repo, "T-001", refine=True, refine_fn=lambda r, k: None)
    assert cell(repo, "T-001", "status") == "NEEDS_HUMAN"


def test_edit_moves_to_draft_and_names_the_brief(repo):
    raise_escalation(repo, "T-001", stop_for())
    path = edit(repo, "T-001")
    assert cell(repo, "T-001", "status") == "DRAFT"
    assert path == repo / "docs" / "work-items" / "T-001.md"
    assert list_open(repo) == []


def test_reject_marks_failed_and_keeps_the_reason(repo):
    raise_escalation(repo, "T-001", stop_for())
    reject(repo, "T-001", reason="wrong approach")
    assert cell(repo, "T-001", "status") == "FAILED"
    assert "wrong approach" in cell(repo, "T-001", "notes")
    assert list_open(repo) == []


@pytest.mark.parametrize("answer", [approve, retry, edit, reject])
def test_every_answer_needs_an_open_escalation(repo, answer):
    with pytest.raises(CommandError, match="not waiting"):
        answer(repo, "T-001")
    assert cell(repo, "T-001", "status") == "IN_PROGRESS"


@pytest.mark.parametrize("answer", [approve, retry, edit, reject])
def test_an_answer_for_an_unknown_key_is_refused(repo, answer):
    with pytest.raises(CommandError, match="T-999"):
        answer(repo, "T-999")


# --- the report ----------------------------------------------------------------------------------


def test_the_report_counts_rates_by_trigger_type_and_overrides(repo):
    raise_escalation(repo, "T-001", stop_for(Trigger.TASK_FLAG, "sign-off"))
    approve(repo, "T-001")
    raise_escalation(repo, "T-002", stop_for(Trigger.LOOP_EXHAUSTED))
    reject(repo, "T-002", reason="no")
    raise_escalation(repo, "T-001", stop_for(Trigger.LOOP_EXHAUSTED))
    data = report(repo)
    flag = data["by_trigger"]["task_flag"]
    assert (flag["raised"], flag["approved"], flag["overridden"]) == (1, 1, 0)
    loop = data["by_trigger"]["loop_exhausted"]
    assert (loop["raised"], loop["rejected"], loop["overridden"]) == (2, 1, 1)
    assert loop["pending"] == 1
    assert data["by_type"] == {"feature": 3}
    assert data["resolved"] == 2
    assert data["overridden"] == 1


def test_rates_are_over_resolved_escalations(repo):
    raise_escalation(repo, "T-001", stop_for(Trigger.TASK_FLAG, "sign-off"))
    approve(repo, "T-001")
    data = report(repo)
    assert data["by_trigger"]["task_flag"]["override_rate"] == 0.0
    raise_escalation(repo, "T-002", stop_for(Trigger.TASK_FLAG, "sign-off"))
    retry(repo, "T-002")
    assert report(repo)["by_trigger"]["task_flag"]["override_rate"] == 0.5


def test_an_empty_report_is_fine(repo):
    data = report(repo)
    assert data["by_trigger"] == {}
    assert "no escalations" in render_report(data)


def test_the_text_report_lists_each_trigger(repo):
    raise_escalation(repo, "T-001", stop_for(Trigger.TASK_FLAG, "sign-off"))
    approve(repo, "T-001")
    text = render_report(report(repo))
    assert "task_flag" in text
    assert "approved unchanged" in text


def test_the_log_is_append_only_json_lines(repo):
    raise_escalation(repo, "T-001", stop_for())
    reject(repo, "T-001")
    lines = (repo / ".adlc" / "escalations" / "log.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["event"] for e in events] == ["raised", "reject"]
    assert {e["trigger"] for e in events} == {"loop_exhausted"}


def test_a_brief_type_is_read_from_the_brief(tmp_path):
    repo = make_repo(
        tmp_path,
        [("T-001", "IN_PROGRESS")],
        {"T-001": brief_text("T-001").replace("type: feature", "type: bugfix")},
    )
    raise_escalation(repo, "T-001", stop_for())
    assert report(repo)["by_type"] == {"bugfix": 1}
