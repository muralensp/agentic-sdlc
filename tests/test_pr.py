"""The PR step: body, checks, the merge conditions and cleanup (ADLC-021)."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from adlc.board import Board
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.escalation import Facts, Trigger, approve, list_open
from adlc.fake_forge import FakeForge
from adlc.fake_runner import FakeRunner, error, ok
from adlc.gitwork import create_worktree
from adlc.pr import (
    REQUIRED_SECTIONS,
    Check,
    GhCli,
    PullRequest,
    build_body,
    deliver,
    merge_blockers,
    read_template,
)
from adlc.review import GateResult, ReviewReport
from adlc.runner import Role
from test_commands import brief_text, make_repo

PASS, FAIL, WAIT = "pass", "fail", "pending"
GOOD_BODY = "## Summary\nDid it.\n## Impacted modules\nsrc\n## Tests\nnew\n## Risks\nnone\n"


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@dataclass
class Scenario:
    repo: Path
    worktree: Path
    settings: object

    def _row(self):
        board = Board(self.repo / "docs" / "WORK-ITEMS.md")
        return next(r for r in board.rows() if r.key == "T-001")

    def state(self) -> str:
        return self._row().cells["Status"]

    def notes(self) -> str:
        return self._row().cells["Notes"]


def make_scenario(tmp_path, *, merge=None, requires_approval=False) -> Scenario:
    brief = brief_text("T-001").replace(
        "requires_approval: false", f"requires_approval: {str(requires_approval).lower()}"
    )
    repo = make_repo(tmp_path, [("T-001", "IN_PROGRESS")], {"T-001": brief})
    (repo / ".adlc" / "config.yaml").write_text(
        yaml.safe_dump({"merge": {"auto_merge": True, **(merge or {})}})
    )
    (repo / ".gitignore").write_text(".adlc/\n")
    g(repo.parent, "init", "-q", "-b", "main", str(repo))
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "init")
    settings = load_config(repo)
    made = create_worktree(
        repo, "T-001", "feature", "the thing", settings.git, root=tmp_path / "wt", fetch=False
    )
    (made.path / "src.txt").write_text("x")
    g(made.path, "add", "-A")
    g(made.path, "commit", "-q", "-m", "feat(core): the thing (T-001)")
    return Scenario(repo, made.path, settings)


@pytest.fixture
def sc(tmp_path):
    return make_scenario(tmp_path)


def approved_report():
    gates = (
        GateResult(1, "Architecture", "Pass", "layers ok"),
        GateResult(2, "Backend", "Pass", "tests added"),
        GateResult(3, "API", "Not Applicable", "no endpoint"),
    )
    return ReviewReport("approve", gates=gates)


def ticking(step=1.0):
    """A clock that moves on every look, so a loop that should end cannot hang the tests."""
    now = iter(x * step for x in range(10**9))
    return lambda: next(now)


def run(sc, forge=None, runner=None, **kw):
    forge = forge if forge is not None else FakeForge([[Check("ci", PASS)]])
    runner = runner if runner is not None else FakeRunner([ok(GOOD_BODY)])
    args = {
        "report": approved_report(),
        "dod_green": True,
        "run_log": ".adlc/logs/T-001/run-1",
        "model": "claude-opus",
        "sleep": lambda s: None,
        "clock": ticking(),
    }
    args.update(kw)
    outcome = deliver(sc.repo, "T-001", sc.worktree, runner=runner, forge=forge, **args)
    return outcome, forge, runner


# --- the merge conditions, one at a time ----------------------------------------------------------


def ready(**kw):
    base = {
        "review": "approve",
        "dod_green": True,
        "checks": (Check("ci", PASS),),
        "required": ("ci",),
        "open_escalation": False,
        "auto_merge": True,
        "approved": False,
    }
    base.update(kw)
    return merge_blockers(**base)


def test_nothing_blocks_when_every_condition_holds():
    assert ready() == []


@pytest.mark.parametrize("decision", ["changes_requested", "escalate", ""])
def test_a_missing_reviewer_approval_blocks(decision):
    assert any("Reviewer" in b for b in ready(review=decision))


def test_a_red_dod_blocks():
    assert any("Definition of Done" in b for b in ready(dod_green=False))


def test_a_red_check_blocks_even_if_not_required():
    blockers = ready(checks=(Check("ci", PASS), Check("lint", FAIL)), required=("ci",))
    assert any("lint" in b and "failed" in b for b in blockers)


def test_a_pending_check_blocks():
    assert any("ci" in b for b in ready(checks=(Check("ci", WAIT),)))


def test_a_required_check_that_has_not_reported_blocks():
    assert any("build" in b for b in ready(required=("ci", "build")))


def test_an_open_escalation_blocks():
    assert any("escalation" in b for b in ready(open_escalation=True))


def test_auto_merge_off_blocks_until_a_person_approves():
    assert any("auto_merge" in b for b in ready(auto_merge=False))
    assert ready(auto_merge=False, approved=True) == []


# --- the body -------------------------------------------------------------------------------------


def body_for(sc, text=GOOD_BODY, **kw):
    runner = FakeRunner([ok(text)] if text is not None else [error("boom")])
    body = build_body(
        sc.repo,
        "T-001",
        sc.worktree,
        sc.settings,
        runner=runner,
        report=kw.pop("report", approved_report()),
        model="claude-opus",
        run_log=".adlc/logs/T-001/run-1",
        **kw,
    )
    return body, runner


def test_the_body_links_the_requirement_and_the_criteria(sc):
    body, _ = body_for(sc)
    assert "T-001" in body
    assert "It works" in body  # an acceptance criterion from the brief
    assert "docs/work-items/T-001.md" in body.replace("\\", "/")


def test_the_body_carries_the_gate_checklist_with_sign_off_lines(sc):
    body, _ = body_for(sc)
    assert "Gate 1 (Architecture): reviewed by claude-opus (agent)" in body
    assert "result Pass" in body
    assert "Gate 3 (API)" in body
    assert "Not Applicable" in body


def test_the_body_links_the_logs(sc):
    body, _ = body_for(sc)
    assert ".adlc/logs/T-001/run-1" in body


def test_the_writer_runs_as_the_pr_writer_and_cannot_edit(sc):
    _, runner = body_for(sc)
    (request,) = runner.requests
    assert request.role is Role.PR_WRITER
    assert "Edit" in request.disallowed_tools
    assert "Write" in request.disallowed_tools
    assert "T-001" in request.prompt


def test_missing_sections_are_added_not_left_out(sc):
    body, _ = body_for(sc, text="## Summary\nDid it.\n")
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in body


def test_a_failed_writer_still_gives_a_complete_body(sc):
    body, _ = body_for(sc, text=None)
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in body
    assert "Gate 1 (Architecture)" in body


def test_the_repos_pr_template_goes_to_the_writer(sc):
    (sc.repo / ".github").mkdir(exist_ok=True)
    (sc.repo / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## What\n## Why\n")
    assert read_template(sc.repo) == "## What\n## Why\n"
    _, runner = body_for(sc, text="## What\nx\n## Why\ny\n", template=read_template(sc.repo))
    assert "## Why" in runner.requests[0].prompt


def test_no_template_reads_as_none(sc):
    assert read_template(sc.repo) is None


# --- the gh command line --------------------------------------------------------------------------


class Recorder:
    def __init__(self, replies=()):
        self.calls, self.replies = [], list(replies)

    def __call__(self, args, cwd=None, text_in=None):
        self.calls.append((list(args), text_in))
        out = self.replies.pop(0) if self.replies else ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def test_gh_creates_the_pr_with_the_body_on_stdin():
    rec = Recorder(["https://github.com/o/r/pull/12\n"])
    pr = GhCli(rec).create_pr(Path("."), base="main", branch="feature/x", title="feat: x", body="B")
    assert pr == PullRequest(12, "https://github.com/o/r/pull/12", "feature/x")
    args, text_in = rec.calls[0]
    assert args[:3] == ["gh", "pr", "create"]
    assert args[args.index("--base") + 1] == "main"
    assert "--body-file" in args
    assert text_in == "B"


def test_gh_reads_check_buckets():
    reply = json.dumps(
        [
            {"name": "a", "bucket": "pass"},
            {"name": "b", "bucket": "skipping"},
            {"name": "c", "bucket": "fail"},
            {"name": "d", "bucket": "cancel"},
            {"name": "e", "bucket": "pending"},
        ]
    )
    checks = GhCli(Recorder([reply])).checks(PullRequest(1, "u", "b"))
    assert [(c.name, c.state) for c in checks] == [
        ("a", PASS),
        ("b", PASS),
        ("c", FAIL),
        ("d", FAIL),
        ("e", WAIT),
    ]


def test_gh_with_no_checks_yet_reports_an_empty_list():
    assert GhCli(Recorder([""])).checks(PullRequest(1, "u", "b")) == []


@pytest.mark.parametrize(("method", "flag"), [("squash", "--squash"), ("rebase", "--rebase")])
def test_gh_merges_without_deleting_the_branch(method, flag):
    rec = Recorder()
    GhCli(rec).merge(PullRequest(5, "u", "b"), method)
    args = rec.calls[0][0]
    assert args[:4] == ["gh", "pr", "merge", "5"]
    assert flag in args
    assert "--delete-branch" not in args  # git cannot delete a branch a worktree has checked out


def test_gh_marks_a_pr_needs_human_and_draft():
    rec = Recorder()
    GhCli(rec).mark_needs_human(PullRequest(5, "u", "b"))
    joined = [" ".join(a) for a, _ in rec.calls]
    assert any("--add-label needs-human" in c for c in joined)
    assert any("pr ready 5 --undo" in c for c in joined)


# --- deliver --------------------------------------------------------------------------------------


def test_a_clean_task_is_pushed_opened_checked_merged_and_cleaned_up(sc):
    outcome, forge, _ = run(sc)
    assert outcome.status == "merged"
    assert forge.names() == ["push", "create_pr", "checks", "merge", "delete_remote_branch"]
    assert not sc.worktree.exists()
    assert sc.state() == "DONE"
    assert "pull/7" in sc.notes()
    assert "run-1" in sc.notes()


def test_the_pr_is_opened_against_the_base_with_the_commit_subject_as_title(sc):
    _, forge, _ = run(sc)
    create = next(c for c in forge.calls if c[0] == "create_pr")
    assert create[1] == "main"
    assert create[3] == "feat(core): the thing (T-001)"
    assert "Gate 1 (Architecture)" in create[4]


def test_the_merge_method_follows_the_config(tmp_path):
    sc = make_scenario(tmp_path, merge={"method": "rebase"})
    _, forge, _ = run(sc)
    assert ("merge", 7, "rebase") in forge.calls


def test_it_waits_for_pending_checks(sc):
    slept = []
    forge = FakeForge([[Check("ci", WAIT)], [Check("ci", WAIT)], [Check("ci", PASS)]])
    outcome, forge, _ = run(sc, forge, sleep=slept.append)
    assert outcome.status == "merged"
    assert len(slept) == 2


def test_checks_that_never_finish_leave_the_pr_open_and_unmerged(sc):
    ticks = iter(range(0, 100_000, 600))
    forge = FakeForge([[Check("ci", WAIT)]])
    outcome, forge, _ = run(sc, forge, clock=lambda: next(ticks), max_wait=1200)
    assert outcome.status == "waiting"
    assert "merge" not in forge.names()
    assert sc.state() == "IN_REVIEW"
    assert sc.worktree.exists()


def test_a_red_check_prevents_the_merge_and_escalates(sc):
    forge = FakeForge([[Check("ci", FAIL)]])
    outcome, forge, _ = run(sc, forge)
    assert outcome.status == "escalated"
    assert "merge" not in forge.names()
    assert ("mark_needs_human", 7) in forge.calls
    assert sc.state() == "NEEDS_HUMAN"
    assert sc.worktree.exists()
    (record,) = list_open(sc.repo)
    assert record["escalations"][0]["trigger"] == Trigger.EXTERNAL_CHECK.value


def test_a_missing_required_check_prevents_the_merge(tmp_path):
    sc = make_scenario(tmp_path, merge={"required_checks": ["ci", "build"]})
    ticks = iter(range(0, 100_000, 600))
    outcome, forge, _ = run(sc, clock=lambda: next(ticks), max_wait=1200)
    assert outcome.status == "waiting"
    assert "merge" not in forge.names()


def test_a_reviewer_who_has_not_approved_stops_before_anything_is_pushed(sc):
    forge = FakeForge()
    with pytest.raises(CommandError, match="Reviewer"):
        run(sc, forge, report=ReviewReport("changes_requested"))
    assert forge.calls == []
    assert sc.state() == "IN_PROGRESS"


def test_a_red_dod_stops_before_anything_is_pushed(sc):
    forge = FakeForge()
    with pytest.raises(CommandError, match="Definition of Done"):
        run(sc, forge, dod_green=False)
    assert forge.calls == []


def test_an_open_trigger_prevents_the_merge(sc):
    facts = Facts(changed=(".github/workflows/ci.yml",))
    outcome, forge, _ = run(sc, facts=facts)
    assert outcome.status == "escalated"
    assert "merge" not in forge.names()
    assert ("mark_needs_human", 7) in forge.calls
    assert sc.state() == "NEEDS_HUMAN"
    assert {e.trigger for e in outcome.escalations} == {Trigger.PROTECTED_AREA}


def test_a_task_flagged_for_approval_waits_then_merges_after_approve(tmp_path):
    sc = make_scenario(tmp_path, requires_approval=True)
    outcome, forge, _ = run(sc)
    assert outcome.status == "escalated"
    assert "merge" not in forge.names()
    approve(sc.repo, "T-001")
    again, forge2, runner2 = run(sc, FakeForge([[Check("ci", PASS)]]))
    assert again.status == "merged"
    assert "create_pr" not in forge2.names()  # the PR already exists
    assert "merge" in forge2.names()
    assert sc.state() == "DONE"
    assert runner2.requests == []  # no second PR text


def test_auto_merge_off_asks_for_approval_every_time(tmp_path):
    sc = make_scenario(tmp_path, merge={"auto_merge": False})
    outcome, forge, _ = run(sc)
    assert outcome.status == "escalated"
    assert "merge" not in forge.names()
    (record,) = list_open(sc.repo)
    assert "auto_merge" in record["escalations"][0]["reason"]


def test_approval_does_not_excuse_a_red_check(tmp_path):
    sc = make_scenario(tmp_path, requires_approval=True)
    run(sc)
    approve(sc.repo, "T-001")
    outcome, forge, _ = run(sc, FakeForge([[Check("ci", FAIL)]]))
    assert outcome.status == "escalated"
    assert "merge" not in forge.names()


def test_nothing_is_deployed(sc):
    _, forge, _ = run(sc)
    assert not [n for n in forge.names() if "deploy" in n or "release" in n]
