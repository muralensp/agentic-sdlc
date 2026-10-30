"""The Claude Code assets package: subagents, an edit-guard hook and deny rules (ADLC-033)."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from adlc.assets import (
    HOOK_PATH,
    AssetsError,
    claude_assets,
    hook_source,
    role_problems,
)
from adlc.cli import main
from adlc.config import load_config
from adlc.fake_forge import FakeForge
from adlc.init import adopt, file_plan
from adlc.methods import DEFAULT_STAGES
from adlc.runner import AgentRequest, ClaudeCodeRunner, Role
from adlc.scaffold import create_repo, scaffold
from adlc.scope import out_of_scope
from test_commands import make_repo
from test_init import make as make_target
from test_methods import doctor, make_skill, pinned_config

AGENT_FILES = [f".claude/agents/{n}.md" for n in ("tester", "coder", "reviewer", "pr-writer")]
SETTINGS = ".claude/settings.json"
PROTECTED_FILE = ".claude/hooks/protected.json"
WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path, [("T-001", "Open")])


def configure(repo: Path, **sections) -> None:
    (repo / ".adlc").mkdir(exist_ok=True)
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump(sections), encoding="utf-8")


def front_matter(text: str) -> dict:
    block = text.split("---")[1]
    return yaml.safe_load(block)


def load_hook(tmp_path: Path):
    path = tmp_path / "guard_edits.py"
    path.write_text(hook_source(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("guard_edits", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- what is produced ---


def test_the_package_has_four_agents_a_hook_its_rules_and_settings(repo):
    files = claude_assets(load_config(repo))
    for path in (*AGENT_FILES, HOOK_PATH, PROTECTED_FILE, SETTINGS):
        assert path in files, path
    assert len(files) == 7


def test_every_agent_names_itself_and_has_a_model_and_tools(repo):
    files = claude_assets(load_config(repo))
    for path in AGENT_FILES:
        meta = front_matter(files[path])
        assert meta["name"] == Path(path).stem
        assert meta["model"] and meta["tools"] and meta["description"]


def test_the_reviewer_can_only_read(repo):
    reviewer = front_matter(claude_assets(load_config(repo))[".claude/agents/reviewer.md"])
    tools = {t.strip() for t in reviewer["tools"].split(",")}
    assert tools == {"Read", "Grep", "Glob"}


def test_the_coder_and_tester_can_edit_and_the_pr_writer_cannot(repo):
    files = claude_assets(load_config(repo))
    for name, can_edit in (("coder", True), ("tester", True), ("pr-writer", False)):
        tools = front_matter(files[f".claude/agents/{name}.md"])["tools"]
        assert ("Edit" in tools) is can_edit, name


def test_the_reviewer_and_the_coder_get_different_models_by_default(repo):
    files = claude_assets(load_config(repo))
    coder = front_matter(files[".claude/agents/coder.md"])["model"]
    reviewer = front_matter(files[".claude/agents/reviewer.md"])["model"]
    assert coder != reviewer


def test_the_models_in_the_config_are_used(repo):
    configure(repo, models={"coder": "haiku", "reviewer": "opus", "tester": "sonnet"})
    files = claude_assets(load_config(repo))
    assert front_matter(files[".claude/agents/coder.md"])["model"] == "haiku"
    assert front_matter(files[".claude/agents/reviewer.md"])["model"] == "opus"
    assert front_matter(files[".claude/agents/tester.md"])["model"] == "sonnet"


def test_the_settings_register_the_hook_for_every_editing_tool(repo):
    data = json.loads(claude_assets(load_config(repo))[SETTINGS])
    (entry,) = data["hooks"]["PreToolUse"]
    assert set(entry["matcher"].split("|")) == set(WRITERS)
    assert HOOK_PATH in entry["hooks"][0]["command"]


def test_the_python_that_runs_the_hook_can_be_chosen(repo):
    data = json.loads(claude_assets(load_config(repo), python="python3")[SETTINGS])
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith("python3 ")


def test_the_deny_rules_come_from_the_profile(repo):
    settings = load_config(repo)
    deny = set(json.loads(claude_assets(settings)[SETTINGS])["permissions"]["deny"])
    assert f"Edit({settings.board.path})" in deny and f"Write({settings.board.path})" in deny
    for glob in settings.protected:
        assert f"Edit({glob})" in deny and f"Write({glob})" in deny, glob
    assert {"WebFetch", "WebSearch", "Bash(curl:*)", "Bash(git push:*)"} <= deny


def test_a_repo_that_changes_the_profile_changes_the_rules(repo):
    configure(repo, protected_extra=["secrets/**"])
    files = claude_assets(load_config(repo))
    assert "Edit(secrets/**)" in json.loads(files[SETTINGS])["permissions"]["deny"]
    assert "secrets/**" in json.loads(files[PROTECTED_FILE])["protected"]


def test_the_rules_file_lists_the_board_and_every_protected_area(repo):
    settings = load_config(repo)
    rules = json.loads(claude_assets(settings)[PROTECTED_FILE])
    assert rules["board"] == settings.board.path
    assert tuple(rules["protected"]) == settings.protected


@pytest.mark.parametrize("stack", ["python", "dotnet"])
def test_everything_in_the_package_is_itself_a_protected_path(tmp_path, stack):
    create_repo(tmp_path / "demo", "demo", stack, "DEMO", claude_assets=False)
    settings = load_config(tmp_path / "demo")
    paths = list(claude_assets(settings))
    assert out_of_scope(paths, settings.protected) == paths


def test_two_agents_on_one_model_are_refused_at_generation(repo):
    with pytest.raises(AssetsError, match="reviewer"):
        # models.reviewer == models.coder is a config error, so build the settings by hand
        import dataclasses

        settings = load_config(repo)
        same = dataclasses.replace(
            settings, models=dataclasses.replace(settings.models, coder="opus", reviewer="opus")
        )
        claude_assets(same)


# --- the hook, one rule at a time ---


@pytest.fixture
def hook(tmp_path):
    return load_hook(tmp_path)


def payload(path, tool="Edit", cwd="/repo"):
    return {"tool_name": tool, "tool_input": {"file_path": path}, "cwd": cwd}


def patterns(repo):
    settings = load_config(repo)
    return (settings.board.path, *settings.protected)


def denied(decision):
    return decision is not None and decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_hook_blocks_the_board(hook, repo):
    assert denied(hook.decide(payload("/repo/docs/WORK-ITEMS.md"), patterns(repo)))


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".adlc/config.yaml",
        ".claude/settings.json",
        ".claude/hooks/guard_edits.py",
        "pyproject.toml",
        "src/pkg/pyproject.toml",
        "package.json",
        "app/migrations/0001_init.py",
        ".env",
        ".env.local",
        "keys/server.pem",
        "config/secrets.json",
        "CLAUDE.md",
        "docs/AGENTS.md",
    ],
)
def test_the_hook_blocks_each_protected_area(hook, repo, path):
    assert denied(hook.decide(payload(f"/repo/{path}"), patterns(repo))), path


@pytest.mark.parametrize("tool", WRITERS)
def test_the_hook_covers_every_editing_tool(hook, repo, tool):
    target = (
        {"notebook_path": "/repo/.env"} if tool == "NotebookEdit" else {"file_path": "/repo/.env"}
    )
    event = {"tool_name": tool, "tool_input": target, "cwd": "/repo"}
    assert denied(hook.decide(event, patterns(repo)))


@pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py", "docs/notes.md", "README.md"])
def test_the_hook_lets_ordinary_files_through(hook, repo, path):
    assert hook.decide(payload(f"/repo/{path}"), patterns(repo)) is None


def test_the_hook_ignores_tools_that_do_not_edit(hook, repo):
    event = {"tool_name": "Read", "tool_input": {"file_path": "/repo/.env"}, "cwd": "/repo"}
    assert hook.decide(event, patterns(repo)) is None


def test_the_hook_understands_windows_paths_and_case(hook, repo):
    assert denied(
        hook.decide(payload("C:\\repo\\.GitHub\\workflows\\ci.yml", cwd="C:\\repo"), patterns(repo))
    )
    assert denied(hook.decide(payload("docs\\work-items.md"), patterns(repo)))


def test_the_hook_refuses_a_path_that_leaves_the_repository(hook, repo):
    assert denied(hook.decide(payload("../outside.txt"), patterns(repo)))


def test_the_denial_says_why(hook, repo):
    reason = hook.decide(payload("/repo/.env"), patterns(repo))["hookSpecificOutput"][
        "permissionDecisionReason"
    ]
    assert ".env" in reason and "orchestrator" in reason


PARITY = [
    ".env",
    "a/.env.local",
    "docs/WORK-ITEMS.md",
    "src/App.py",
    "src/x/migrations/001.py",
    "migrations/001.py",
    "x.pem",
    "deep/er/pyproject.toml",
    "pyproject.toml.bak",
    ".github",
    ".github/a/b/c.yml",
    "CLAUDE.md",
    "sub/CLAUDE.md",
    "sub/AGENTS.md",
    "AGENTS.md",
    "src/main.cs",
    "app/App.csproj",
]


def test_the_hook_matches_paths_exactly_as_the_orchestrator_does(hook, repo):
    settings = load_config(repo)
    pats = (settings.board.path, *settings.protected)
    for path in PARITY:
        by_hook = denied(hook.decide(payload(f"/repo/{path}"), pats))
        by_orchestrator = bool(out_of_scope([path], pats))
        assert by_hook == by_orchestrator, path


def test_the_generated_hook_runs_as_a_script(tmp_path, repo):
    files = claude_assets(load_config(repo))
    for path, text in files.items():
        target = tmp_path / "work" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    script = tmp_path / "work" / HOOK_PATH

    def run(path):
        event = json.dumps(payload(str(tmp_path / "work" / path), cwd=str(tmp_path / "work")))
        return subprocess.run(
            [sys.executable, str(script)], input=event, capture_output=True, text=True, timeout=60
        )

    blocked = run("docs/WORK-ITEMS.md")
    assert blocked.returncode == 0 and "deny" in blocked.stdout
    allowed = run("src/app.py")
    assert allowed.returncode == 0 and allowed.stdout.strip() == ""


# --- the doctor ---


def write_agents(repo: Path, **models) -> None:
    for name, model in models.items():
        path = repo / ".claude" / "agents" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\nmodel: {model}\ntools: Read\n---\nbody\n")


def test_no_agent_files_is_not_a_problem(repo):
    assert role_problems(repo) == []


def test_different_models_pass(repo):
    write_agents(repo, coder="sonnet", reviewer="opus")
    assert role_problems(repo) == []


def test_the_same_model_for_coder_and_reviewer_is_a_problem(repo):
    write_agents(repo, coder="opus", reviewer="opus")
    (problem,) = role_problems(repo)
    assert "reviewer" in problem and "coder" in problem


def test_an_alias_and_its_full_id_count_as_the_same_model(repo):
    write_agents(repo, coder="claude-opus-5-5", reviewer="opus")
    assert role_problems(repo)


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "skills"
    for skill in {s for s, _ in DEFAULT_STAGES.values()}:
        make_skill(root, skill, f"instructions for {skill}\n")
    return root


def test_doctor_passes_when_the_roles_use_different_models(repo, skills, capsys):
    config = pinned_config(repo, skills)
    write_agents(repo, coder="sonnet", reviewer="opus")
    assert doctor(repo, config, skills) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_doctor_fails_when_the_reviewer_shares_the_coders_model(repo, skills, capsys):
    config = pinned_config(repo, skills)
    assert doctor(repo, config, skills) == 0  # the methods themselves are fine
    capsys.readouterr()
    write_agents(repo, coder="opus", reviewer="opus")
    assert doctor(repo, config, skills) == 1
    assert "FAIL" in capsys.readouterr().out


# --- the runner picks the agent ---


def runner(repo):
    return ClaudeCodeRunner(load_config(repo).models)


def arguments(repo, role, cwd, **kw):
    request = AgentRequest(role, "prompt", cwd, **kw)
    return runner(repo)._arguments(request, None, None)


@pytest.mark.parametrize(
    ("role", "agent"),
    [
        (Role.TESTER, "tester"),
        (Role.CODER, "coder"),
        (Role.REVIEWER, "reviewer"),
        (Role.PR_WRITER, "pr-writer"),
    ],
)
def test_a_role_runs_as_its_agent_when_the_repo_defines_it(repo, tmp_path, role, agent):
    work = tmp_path / "work"
    for path, text in claude_assets(load_config(repo)).items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    args = arguments(repo, role, work)
    assert args[args.index("--agent") + 1] == agent


def test_without_agent_files_the_runner_adds_no_agent(repo, tmp_path):
    assert "--agent" not in arguments(repo, Role.CODER, tmp_path)


def test_an_agent_named_in_the_request_wins(repo, tmp_path):
    work = tmp_path / "work"
    (work / ".claude" / "agents").mkdir(parents=True)
    (work / ".claude" / "agents" / "coder.md").write_text("---\nname: coder\n---\n")
    args = arguments(repo, Role.CODER, work, agent="special")
    assert args[args.index("--agent") + 1] == "special"


# --- init and new ---


def test_init_leaves_claude_alone_unless_asked(tmp_path):
    target = make_target(tmp_path, {"README.md": "x"})
    assert not [c for c in file_plan(target) if c.path.startswith(".claude/")]


def test_init_can_add_the_package(tmp_path):
    target = make_target(tmp_path, {"README.md": "x"})
    plan = file_plan(target, claude_assets=True)
    added = {c.path for c in plan if c.path.startswith(".claude/")}
    assert {*AGENT_FILES, HOOK_PATH, PROTECTED_FILE, SETTINGS} == added


def test_init_never_replaces_a_settings_file_that_exists(tmp_path):
    target = make_target(tmp_path, {"README.md": "x", ".claude/settings.json": '{"mine": 1}'})
    paths = {c.path for c in file_plan(target, claude_assets=True)}
    assert SETTINGS not in paths and HOOK_PATH in paths


def test_init_delivers_the_package_in_the_adoption_pr(tmp_path):
    target = make_target(tmp_path, {"README.md": "x"})
    done = adopt(
        target,
        confirm=True,
        forge=FakeForge(),
        root=tmp_path / "wt",
        fetch=False,
        claude_assets=True,
    )
    changed = subprocess.run(
        ["git", "diff", "--name-only", "main", done.branch],
        cwd=target,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert SETTINGS in changed and ".claude/agents/reviewer.md" in changed


def test_the_init_command_takes_the_flag(tmp_path, capsys):
    target = make_target(tmp_path, {"README.md": "x"})
    assert main(["init", "--claude-assets", "--repo", str(target)]) == 0
    assert ".claude/settings.json" in capsys.readouterr().out
    assert not (target / ".claude").exists()  # still a dry run


def test_new_includes_the_package_by_default(tmp_path):
    files = scaffold("demo", "python", "DEMO")
    for path in (*AGENT_FILES, HOOK_PATH, PROTECTED_FILE, SETTINGS):
        assert path in files, path


def test_new_can_leave_it_out(tmp_path):
    files = scaffold("demo", "python", "DEMO", claude_assets=False)
    assert not [p for p in files if p.startswith(".claude/")]


def test_new_command_flag(tmp_path, capsys):
    assert (
        main(["new", "demo", "--stack", "python", "--dest", str(tmp_path), "--no-claude-assets"])
        == 0
    )
    assert ".claude/settings.json" not in capsys.readouterr().out


def test_a_new_repos_rules_follow_its_own_profile(tmp_path):
    files = scaffold("demo", "dotnet", "DEMO")
    rules = json.loads(files[PROTECTED_FILE])
    assert "**/*.csproj" in rules["protected"] and "docs/AGENTS.md" not in rules["protected"]
    assert re.search(r"\*\*/AGENTS\.md", " ".join(rules["protected"]))


def test_a_coder_set_to_the_reviewers_default_pushes_the_reviewer_elsewhere(repo):
    configure(repo, models={"coder": "opus"})
    files = claude_assets(load_config(repo))
    coder = front_matter(files[".claude/agents/coder.md"])["model"]
    reviewer = front_matter(files[".claude/agents/reviewer.md"])["model"]
    assert coder == "opus" and reviewer != "opus"


def test_new_command_includes_the_package_unless_told_not_to(tmp_path, capsys):
    assert main(["new", "demo", "--stack", "python", "--dest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert ".claude/settings.json" in out and ".claude/hooks/guard_edits.py" in out
