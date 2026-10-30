"""`adlc new`: scaffold a fresh repository for a stack (ADLC-025)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from adlc.cli import main
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.dod import find_bash
from adlc.pr import REQUIRED_SECTIONS
from adlc.review import GATES
from adlc.scaffold import ScaffoldError, create_repo, render_scaffold, scaffold

ROOT = Path(__file__).resolve().parents[1]
RUN_DOTNET = os.environ.get("ADLC_DOTNET_BUILD")

COMMON = [
    ".gitattributes",
    ".gitignore",
    "docs/WORK-ITEMS.md",
    "docs/work-items/.gitkeep",
    "CLAUDE.md",
    "AGENTS.md",
    "GLOSSARY.md",
    "adr/TEMPLATE.md",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".adlc/config.yaml",
    ".adlc/.gitignore",
    ".adlc/wizards/protect-main.sh",
]


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


# --- what is produced -------------------------------------------------------------------------


@pytest.mark.parametrize("stack", ["python", "dotnet"])
def test_every_stack_gets_the_common_files(stack):
    files = scaffold("demo-app", stack, "DEMO")
    for path in COMMON:
        assert path in files, path
    assert any(p.startswith("docs/quality-gates/") for p in files)
    assert any(p.startswith("adr/0001-") for p in files)


def test_python_gets_a_package_and_a_test():
    files = scaffold("demo-app", "python", "DEMO")
    for path in ("pyproject.toml", "src/demo_app/__init__.py", "tests/test_smoke.py"):
        assert path in files, path


def test_dotnet_gets_a_solution_projects_and_the_two_scripts():
    files = scaffold("demo-app", "dotnet", "DEMO")
    for path in (
        "DemoApp.sln",
        "src/DemoApp/DemoApp.csproj",
        "tests/DemoApp.Tests/DemoApp.Tests.csproj",
        "build/scripts/lint.sh",
        "build/scripts/test.sh",
    ):
        assert path in files, path


def test_bad_names_stacks_and_codes_are_refused():
    for bad in ("", "Has Space", "../x", "UPPER", "-lead"):
        with pytest.raises(ScaffoldError, match="name"):
            scaffold(bad, "python", "DEMO")
    with pytest.raises(ScaffoldError, match="stack"):
        scaffold("demo", "cobol", "DEMO")
    with pytest.raises(ScaffoldError, match="code"):
        scaffold("demo", "python", "lower")


def test_no_placeholder_is_left_unfilled():
    for stack in ("python", "dotnet"):
        for path, text in scaffold("demo-app", stack, "DEMO").items():
            assert "{{" not in text and "}}" not in text.replace("${{", ""), path


def test_the_pr_template_has_the_sections_the_pr_step_needs():
    text = scaffold("demo", "python", "DEMO")[".github/pull_request_template.md"]
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in text


def test_there_is_one_gate_checklist_per_gate():
    files = scaffold("demo", "python", "DEMO")
    lists = [p for p in files if p.startswith("docs/quality-gates/")]
    assert len(lists) == len(GATES)
    for gate in GATES:
        assert any(gate.name.lower() in p for p in lists), gate.name


def test_the_first_adr_records_the_stack_choice():
    files = scaffold("demo", "dotnet", "DEMO")
    adr = files["adr/0001-stack-dotnet.md"]
    assert "dotnet" in adr and "## Decision" in adr


def test_the_board_starts_empty_with_the_context_code():
    text = scaffold("demo", "python", "DEMO")["docs/WORK-ITEMS.md"]
    assert "DEMO" in text
    assert "| Key | Context | Title | Status | Linked PRD Chapter / Day | Notes |" in text
    assert not re.search(r"^\| DEMO-\d+", text, re.MULTILINE)


def test_the_wizard_is_the_one_adlc_uses_for_itself():
    ours = (ROOT / ".adlc" / "wizards" / "protect-main.sh").read_bytes()
    assert scaffold("demo", "python", "DEMO")[".adlc/wizards/protect-main.sh"].encode() == ours


def test_the_config_extends_the_profile():
    py = yaml.safe_load(scaffold("demo", "python", "DEMO")[".adlc/config.yaml"])
    assert py["extends"] == "python" and py["board"] == {"code": "DEMO"}
    net = yaml.safe_load(scaffold("demo", "dotnet", "DEMO")[".adlc/config.yaml"])
    assert net["extends"] == "dotnet"
    assert net["dod"]["smoke"] is None and "lint" not in net["dod"]  # scripts it does not have
    assert net["merge"] == {"required_checks": ["CI passed"]}


@pytest.mark.parametrize("stack", ["python", "dotnet"])
def test_the_scaffold_is_a_valid_adlc_repository(tmp_path, stack):
    dest = tmp_path / "demo"
    create_repo(dest, "demo", stack, "DEMO")
    settings = load_config(dest)
    assert settings.board.code == "DEMO"
    assert settings.git.base_branch == "main"
    assert settings.merge.required_checks == ("CI passed",)
    assert {"lint", "test"} <= set(settings.dod)


# --- creating it ------------------------------------------------------------------------------


def test_create_makes_a_git_repo_with_one_commit_on_main(tmp_path):
    dest = tmp_path / "demo"
    done = create_repo(dest, "demo", "python", "DEMO")
    assert g(dest, "branch", "--show-current") == "main"
    assert g(dest, "rev-list", "--count", "HEAD") == "1"
    assert g(dest, "status", "--porcelain") == ""
    assert sorted(done.files) == sorted(g(dest, "ls-files").split("\n"))
    assert g(dest, "log", "-1", "--format=%s").startswith("chore: scaffold")


def test_scripts_are_committed_as_executable(tmp_path):
    dest = tmp_path / "demo"
    create_repo(dest, "demo", "dotnet", "DEMO")
    modes = {line.split("\t")[1]: line.split()[0] for line in g(dest, "ls-files", "-s").split("\n")}
    assert modes[".adlc/wizards/protect-main.sh"] == "100755"
    assert modes["build/scripts/test.sh"] == "100755"
    assert modes["CLAUDE.md"] == "100644"


def test_create_never_runs_the_wizard_or_adds_a_remote(tmp_path):
    dest = tmp_path / "demo"
    create_repo(dest, "demo", "python", "DEMO")
    assert g(dest, "remote") == ""


def test_create_refuses_a_folder_that_is_not_empty(tmp_path):
    dest = tmp_path / "demo"
    dest.mkdir()
    (dest / "mine.txt").write_text("keep")
    with pytest.raises(CommandError, match="not empty"):
        create_repo(dest, "demo", "python", "DEMO")
    assert (dest / "mine.txt").read_text() == "keep"
    assert not (dest / ".git").exists()


def test_an_empty_existing_folder_is_fine(tmp_path):
    dest = tmp_path / "demo"
    dest.mkdir()
    create_repo(dest, "demo", "python", "DEMO")
    assert (dest / "CLAUDE.md").is_file()


def test_files_keep_unix_line_endings(tmp_path):
    dest = tmp_path / "demo"
    create_repo(dest, "demo", "python", "DEMO")
    for path in ("CLAUDE.md", ".adlc/wizards/protect-main.sh", "pyproject.toml"):
        assert b"\r\n" not in (dest / path).read_bytes(), path


# --- the command line -------------------------------------------------------------------------


def test_new_is_a_dry_run_unless_told_otherwise(tmp_path, capsys):
    code = main(["new", "demo", "--stack", "python", "--dest", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "pyproject.toml" in out and "dry run" in out.lower()
    assert not (tmp_path / "demo").exists()


def test_new_yes_creates_the_repo_and_points_at_the_wizard(tmp_path, capsys):
    code = main(["new", "demo", "--stack", "python", "--dest", str(tmp_path), "--yes"])
    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "demo" / ".git").is_dir()
    assert ".adlc/wizards/protect-main.sh" in out
    assert main(["status", "--repo", str(tmp_path / "demo")]) == 0


def test_the_code_defaults_from_the_name(tmp_path, capsys):
    main(["new", "demo-app", "--stack", "python", "--dest", str(tmp_path), "--yes"])
    assert load_config(tmp_path / "demo-app").board.code == "DEMO"


def test_bad_input_exits_1_without_writing(tmp_path, capsys):
    assert main(["new", "Bad Name", "--stack", "python", "--dest", str(tmp_path), "--yes"]) == 1
    assert "name" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_render_lists_every_file_and_says_nothing_is_written():
    text = render_scaffold("demo", "python", sorted(scaffold("demo", "python", "DEMO")))
    assert "pyproject.toml" in text and "nothing is written" in text.lower()


# --- the new repo passes its own checks -----------------------------------------------------------


def sh(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=cwd, capture_output=True, text=True, timeout=600
    )


def test_a_new_python_repo_passes_the_checks_its_ci_runs(tmp_path):
    dest = tmp_path / "demo-app"
    create_repo(dest, "demo-app", "python", "DEMO")
    for args in (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("pytest", "--cov", "--cov-fail-under=80", "-q", "-p", "no:cacheprovider"),
    ):
        done = sh(dest, *args)
        assert done.returncode == 0, f"{args}\n{done.stdout}\n{done.stderr}"


@pytest.mark.skipif(not RUN_DOTNET, reason="set ADLC_DOTNET_BUILD=1 (needs the .NET SDK, network)")
def test_a_new_dotnet_repo_builds_and_passes_its_scripts(tmp_path):
    dest = tmp_path / "demo-app"
    create_repo(dest, "demo-app", "dotnet", "DEMO")
    for command in (
        ["dotnet", "build"],
        [find_bash(), "build/scripts/lint.sh"],
        [find_bash(), "build/scripts/test.sh"],
    ):
        done = subprocess.run(command, cwd=dest, capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, f"{command}\n{done.stdout}\n{done.stderr}"
