"""`adlc init`: look at a repository, propose a config, show the plan, deliver a PR (ADLC-024)."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from adlc.cli import main
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.fake_forge import FakeForge
from adlc.gitwork import create_worktree
from adlc.init import (
    POINTER_MARK,
    adopt,
    detect,
    file_plan,
    gh_protection,
    propose_config,
    render_adoption,
)

BOSS = os.environ.get("ADLC_BOSS_ROOT")


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def make(tmp_path: Path, files: dict[str, str], *, branch="main", commit=True) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    g(repo.parent, "init", "-q", "-b", branch, str(repo))
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    if commit:
        g(repo, "add", "-A")
        g(repo, "commit", "-q", "-m", "init", "--allow-empty")
    return repo


DOTNET = {
    "App.sln": "",
    "src/Api/Api.csproj": "<Project/>",
    "build/scripts/lint.sh": "",
    "build/scripts/test.sh": "",
    "build/scripts/integration-test.sh": "",
    "build/scripts/dependency-scan.sh": "",
    "build/scripts/secret-scan.sh": "",
    "build/scripts/smoke-test.sh": "",
    "build/scripts/e2e-smoke-test.sh": "",
    "build/scripts/contract-test.sh": "",
}


# --- detection --------------------------------------------------------------------------------


def test_a_dotnet_repo_gets_the_dotnet_profile_with_no_overrides(tmp_path):
    found = detect(make(tmp_path, DOTNET))
    assert found.profile == "dotnet"
    assert found.overrides == {}


def test_a_python_repo_gets_the_python_profile(tmp_path):
    assert detect(make(tmp_path, {"pyproject.toml": "[project]\nname='x'\n"})).profile == "python"


def test_an_unknown_stack_gets_the_generic_profile(tmp_path):
    assert detect(make(tmp_path, {"README.md": "hi"})).profile == "generic"


def test_a_script_the_profile_names_but_the_repo_lacks_is_dropped(tmp_path):
    files = {k: v for k, v in DOTNET.items() if "contract" not in k}
    found = detect(make(tmp_path, files))
    assert found.overrides == {"dod": {"contract": None}}


def test_package_json_scripts_become_the_test_and_lint_commands(tmp_path):
    pkg = '{"scripts": {"test": "jest", "lint": "eslint ."}}'
    found = detect(make(tmp_path, {"package.json": pkg}))
    assert found.profile == "generic"
    assert found.overrides["dod"] == {"test": "npm test", "lint": "npm run lint"}


def test_the_default_branch_is_read_from_the_repo(tmp_path):
    found = detect(make(tmp_path, {"README.md": "x"}, branch="master"))
    assert found.base_branch == "master"
    assert found.overrides["git"] == {"base_branch": "master"}


def test_what_the_repo_already_has_is_reported(tmp_path):
    repo = make(
        tmp_path,
        {
            ".github/workflows/ci.yml": "name: CI",
            ".github/pull_request_template.md": "## Summary",
            "adr/0001-x.md": "# x",
            "CONTRIBUTING.md": "# c",
            "CLAUDE.md": "# rules",
            "GLOSSARY.md": "# g",
            "docs/WORK-ITEMS.md": "| a |",
        },
    )
    found = detect(repo)
    assert found.ci == (".github/workflows/ci.yml",)
    assert found.pr_template == ".github/pull_request_template.md"
    assert found.adr_dir == "adr"
    assert found.contributing and found.claude_md and found.glossary
    assert found.board == "docs/WORK-ITEMS.md"


def test_a_board_in_another_place_is_pointed_at(tmp_path):
    found = detect(make(tmp_path, {"WORK-ITEMS.md": "| a |"}))
    assert found.board == "WORK-ITEMS.md"
    assert found.overrides["board"] == {"path": "WORK-ITEMS.md"}


def test_protected_branches_come_from_the_host_when_it_answers(tmp_path):
    repo = make(tmp_path, {"README.md": "x"})
    assert detect(repo, protection=lambda branch: True).base_protected is True
    assert detect(repo, protection=lambda branch: False).base_protected is False
    assert detect(repo).base_protected is None  # unknown, not guessed


# --- the proposed config ------------------------------------------------------------------------


def test_the_proposed_config_loads_and_matches_the_profile(tmp_path):
    repo = make(tmp_path, DOTNET)
    text = propose_config(detect(repo))
    (repo / ".adlc").mkdir()
    (repo / ".adlc" / "config.yaml").write_text(text)
    assert yaml.safe_load(text) == {"extends": "dotnet"}
    assert load_config(repo).dod["test"] == "bash build/scripts/test.sh"


def test_overrides_are_written_into_the_proposal(tmp_path):
    repo = make(tmp_path, {k: v for k, v in DOTNET.items() if "contract" not in k})
    data = yaml.safe_load(propose_config(detect(repo)))
    assert data == {"extends": "dotnet", "dod": {"contract": None}}


# --- the file plan ------------------------------------------------------------------------------


def paths(plan):
    return sorted(c.path for c in plan)


def test_a_bare_repo_is_given_only_the_allowed_files(tmp_path):
    plan = file_plan(make(tmp_path, {"README.md": "x"}))
    assert paths(plan) == [
        ".adlc/.gitignore",
        ".adlc/config.yaml",
        "CLAUDE.md",
        "GLOSSARY.md",
        "docs/work-items/.gitkeep",
    ]
    assert {c.action for c in plan} == {"add"}


def test_an_existing_claude_md_gets_a_pointer_appended_and_nothing_removed(tmp_path):
    repo = make(tmp_path, {"CLAUDE.md": "# Rules\n\nBe kind.\n", "GLOSSARY.md": "# g\n"})
    change = next(c for c in file_plan(repo) if c.path == "CLAUDE.md")
    assert change.action == "change"
    assert change.content.startswith("# Rules\n\nBe kind.\n")
    assert POINTER_MARK in change.content


def test_an_existing_pointer_is_not_added_twice(tmp_path):
    repo = make(tmp_path, {"CLAUDE.md": f"# Rules\n\n{POINTER_MARK}\n"})
    assert "CLAUDE.md" not in paths(file_plan(repo))


def test_an_existing_glossary_and_config_are_left_alone(tmp_path):
    repo = make(tmp_path, {"GLOSSARY.md": "# mine\n", ".adlc/config.yaml": "extends: python\n"})
    plan = paths(file_plan(repo))
    assert "GLOSSARY.md" not in plan
    assert ".adlc/config.yaml" not in plan


def test_the_plan_names_every_file_and_the_detected_profile(tmp_path):
    repo = make(tmp_path, DOTNET)
    text = render_adoption(detect(repo), file_plan(repo))
    assert "dotnet" in text
    for path in paths(file_plan(repo)):
        assert path in text
    assert "nothing is written" in text.lower()


# --- adopting -----------------------------------------------------------------------------------


def adopt_it(tmp_path, repo, forge=None, **kw):
    forge = forge if forge is not None else FakeForge()
    done = adopt(repo, forge=forge, root=tmp_path / "wt", fetch=False, **kw)
    return done, forge


def test_nothing_is_written_without_confirmation(tmp_path):
    repo = make(tmp_path, {"README.md": "x"})
    with pytest.raises(CommandError, match="confirm"):
        adopt_it(tmp_path, repo, confirm=False)
    assert g(repo, "status", "--porcelain") == ""
    assert not (repo / ".adlc").exists()


def test_adopting_commits_only_the_plan_on_a_branch_and_opens_a_pr(tmp_path):
    repo = make(tmp_path, {"README.md": "x", "CLAUDE.md": "# Rules\n"})
    before = g(repo, "rev-parse", "main")
    done, forge = adopt_it(tmp_path, repo, confirm=True)
    assert done.branch == "docs/adlc-init"
    assert done.pr is not None and done.pr.url.endswith("/7")
    assert g(repo, "rev-parse", "main") == before  # never a direct commit to the base
    changed = g(repo, "diff", "--name-only", "main", done.branch).split()
    assert sorted(changed) == [
        ".adlc/.gitignore",
        ".adlc/config.yaml",
        "CLAUDE.md",
        "GLOSSARY.md",
        "docs/work-items/.gitkeep",
    ]
    assert forge.names() == ["push", "create_pr"]
    assert forge.calls[1][1] == "main"
    assert g(repo, "status", "--porcelain") == ""  # the primary checkout is untouched
    assert not (tmp_path / "wt" / "ADLC-000").exists()  # the temporary worktree is gone


def test_a_repo_with_nothing_to_add_has_nothing_to_deliver(tmp_path):
    repo = make(tmp_path, {"README.md": "x"})
    adopt_it(tmp_path, repo, confirm=True)
    g(repo, "checkout", "-q", "docs/adlc-init")
    g(repo, "checkout", "-q", "main")
    g(repo, "merge", "-q", "docs/adlc-init")
    g(repo, "branch", "-q", "-D", "docs/adlc-init")
    with pytest.raises(CommandError, match="nothing to add"):
        adopt_it(tmp_path, repo, confirm=True)


def test_without_a_remote_the_branch_is_committed_and_no_pr_is_opened(tmp_path):
    repo = make(tmp_path, {"README.md": "x"})
    done, forge = adopt_it(tmp_path, repo, confirm=True, remote=False)
    assert done.pr is None
    assert forge.calls == []
    assert g(repo, "rev-list", "--count", f"main..{done.branch}") == "1"


# --- BOSS NextGen (local only, read-only) -------------------------------------------------------


@pytest.mark.skipif(not BOSS, reason="set ADLC_BOSS_ROOT to BOSS NextGen's repository folder")
def test_boss_gets_the_dotnet_profile_and_matches_contributing_with_no_edits():
    root = Path(BOSS)
    found = detect(root)
    assert found.profile == "dotnet"
    assert found.overrides == {}
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for command in load_config_for("dotnet").dod.values():
        for script in [w for w in command.split() if w.endswith(".sh")]:
            assert script.split("/")[-1] in contributing, script


def load_config_for(profile: str):
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        repo = Path(folder)
        (repo / ".adlc").mkdir()
        (repo / ".adlc" / "config.yaml").write_text(f"extends: {profile}\n")
        return load_config(repo)


# --- more edge cases and the command line ---------------------------------------------------------


def test_a_claude_md_with_windows_line_endings_keeps_them(tmp_path):
    repo = make(tmp_path, {"CLAUDE.md": "# Rules\r\n\r\nBe kind.\r\n"})
    change = next(c for c in file_plan(repo) if c.path == "CLAUDE.md")
    assert change.content.startswith("# Rules\r\n\r\nBe kind.\r\n")
    assert "\n" not in change.content.replace("\r\n", "")


def test_gh_protection_reads_the_hosts_answer(tmp_path):
    def reply(code, err=""):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, "", err)

    assert gh_protection(tmp_path, reply(0))("main") is True
    assert gh_protection(tmp_path, reply(1, "Branch not protected (HTTP 404)"))("main") is False
    assert gh_protection(tmp_path, reply(1, "gh: not logged in"))("main") is None

    def no_gh(*a, **k):
        raise FileNotFoundError

    assert gh_protection(tmp_path, no_gh)("main") is None


def test_init_is_a_dry_run_unless_told_otherwise(tmp_path, capsys):
    repo = make(tmp_path, DOTNET)
    assert main(["init", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "dotnet" in out and ".adlc/config.yaml" in out
    assert g(repo, "status", "--porcelain") == ""
    assert not (repo / ".adlc").exists()
    assert g(repo, "branch", "--format=%(refname:short)") == "main"


def test_init_yes_opens_the_pull_request(tmp_path, capsys, monkeypatch):
    repo = make(tmp_path, DOTNET)
    forge = FakeForge()
    monkeypatch.setattr("adlc.cli.GhCli", lambda: forge)
    monkeypatch.setattr("adlc.cli.gh_protection", lambda repo: lambda branch: None)
    monkeypatch.setattr("adlc.init.create_worktree", _no_fetch(create_worktree))
    assert main(["init", "--yes", "--repo", str(repo)]) == 0
    assert "https://example.test/pull/7" in capsys.readouterr().out
    assert forge.names() == ["push", "create_pr"]


def _no_fetch(real):
    return lambda *a, **k: real(*a, **{**k, "fetch": False})
