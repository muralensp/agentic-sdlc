"""`adlc run --dry-run`: the plan for a task, with no model call and no git change (ADLC-018)."""

import subprocess
from pathlib import Path

import pytest

from adlc.cli import main
from adlc.commands import CommandError
from adlc.loop import INSTRUCTIONS
from adlc.plan import plan_run, render_plan
from test_commands import make_repo


def brief(key, *, kind="feature", deps="[]"):
    return (
        f"---\nkey: {key}\ntype: {kind}\npriority: 1\ndepends_on: {deps}\nadr: none\n"
        f"requires_approval: false\n---\n# {key}: Make it work\n\n"
        "**Description:** Do the thing.\n\n**Acceptance criteria:**\n- It works\n- It is fast\n"
    )


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@pytest.fixture
def repo(tmp_path):
    repo = make_repo(
        tmp_path,
        [("T-001", "DONE"), ("T-002", "BACKLOG"), ("T-003", "BACKLOG"), ("T-004", "BACKLOG")],
        {
            "T-002": brief("T-002"),
            "T-003": brief("T-003", kind="bugfix"),
            "T-004": brief("T-004", deps="[T-003]"),
        },
    )
    (repo / "AGENTS.md").write_text("Never touch the secrets folder.\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def snapshot(repo: Path) -> dict:
    return {
        "status": git(repo, "status", "--porcelain", "--ignored"),
        "head": git(repo, "rev-parse", "HEAD"),
        "branches": git(repo, "branch", "--list", "--all"),
        "worktrees": git(repo, "worktree", "list", "--porcelain"),
        "logs": (repo / ".adlc" / "logs").exists(),
    }


def test_the_plan_names_the_task_branch_and_worktree(repo):
    plan = plan_run(repo, "T-002")
    assert (plan.key, plan.title, plan.type) == ("T-002", "Title of T-002", "feature")
    assert plan.branch == "feature/T-002-title-of-t-002"
    assert plan.worktree.name == "T-002" and plan.worktree.parent.name == repo.name
    assert plan.base == "main" and plan.ready


def test_the_coder_prompt_carries_the_brief_and_the_project_rules(repo):
    plan = plan_run(repo, "T-002")
    for text in ("Do the thing.", "It is fast", "Never touch the secrets folder."):
        assert text in plan.coder_prompt
    assert "adr: none" not in plan.coder_prompt  # the front-matter is not pasted in
    assert INSTRUCTIONS in plan.coder_prompt


def test_the_tester_prompt_asks_for_failing_tests_only(repo):
    plan = plan_run(repo, "T-002")
    assert "Do the thing." in plan.tester_prompt
    assert (
        "Write failing tests" in plan.tester_prompt
        and "never production code" in plan.tester_prompt
    )


def test_a_bugfix_plan_starts_with_a_reproducing_test_and_the_diagnose_method(repo):
    plan = plan_run(repo, "T-003")
    assert plan.branch.startswith("bugfix/T-003-")
    assert "reproduces" in plan.tester_prompt and "do not fix the bug" in plan.tester_prompt
    assert plan.methods["tester"].stage == "diagnose"


def test_the_methods_each_stage_would_use_are_listed(repo):
    plan = plan_run(repo, "T-002")
    assert set(plan.methods) == {"tester", "coder", "diagnose", "reviewer"}
    assert plan.methods["coder"].stage == "implement"
    assert plan.methods["reviewer"].stage == "review"
    assert plan.methods["coder"].source in ("fallback", "skill", "none")


def test_a_task_waiting_on_a_dependency_is_planned_but_not_ready(repo):
    plan = plan_run(repo, "T-004")
    assert not plan.ready


def test_a_done_or_unknown_task_cannot_be_planned(repo):
    with pytest.raises(CommandError, match="T-001"):
        plan_run(repo, "T-001")
    with pytest.raises(CommandError, match="T-099"):
        plan_run(repo, "T-099")


def test_the_rendered_plan_shows_everything_a_person_checks(repo):
    text = render_plan(plan_run(repo, "T-002"))
    for needle in ("T-002", "feature/T-002-title-of-t-002", "Tester prompt", "Coder prompt"):
        assert needle in text


def test_a_dry_run_changes_nothing_in_git_and_writes_no_log(repo, capsys):
    before = snapshot(repo)
    code = main(["run", "T-002", "--dry-run", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert code == 0 and "feature/T-002-title-of-t-002" in out and "Coder prompt" in out
    assert snapshot(repo) == before


def test_running_for_real_is_not_built_yet_and_says_so(repo, capsys):
    before = snapshot(repo)
    code = main(["run", "T-002", "--repo", str(repo)])
    err = capsys.readouterr().err
    assert code == 1 and "--dry-run" in err
    assert snapshot(repo) == before


def test_a_dry_run_of_an_unknown_task_is_an_error(repo, capsys):
    code = main(["run", "T-099", "--dry-run", "--repo", str(repo)])
    assert code == 1 and "T-099" in capsys.readouterr().err
