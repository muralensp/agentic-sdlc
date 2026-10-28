"""`adlc refine`: the grilling session launcher and its gates (ADLC-010)."""

import subprocess
from pathlib import Path

import pytest
import yaml

from adlc.commands import CommandError
from adlc.refine import Outcome, refine
from test_commands import board_text, brief_text, make_repo

SKELETON = (
    "---\nkey: {key}\ntype: feature\npriority: 3\ndepends_on: []\nadr: none\n"
    "requires_approval: false\n---\n# {key}: Idea\n\n**Description:** TBD\n\n"
    "**Acceptance criteria:**\n- TBD\n"
)
BRIEF = "docs/work-items/T-002.md"
PROPOSALS = ".adlc/proposals/T-002.md"


@pytest.fixture(autouse=True)
def isolated_skills(tmp_path, monkeypatch):
    """No installed plugin is found, so the grilling fallback prompt is used."""
    monkeypatch.setenv("ADLC_CLAUDE_HOME", str(tmp_path / "claude-home"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = make_repo(tmp_path, [("T-001", "DONE"), ("T-002", "DRAFT")], {})
    (path / BRIEF).write_text(SKELETON.format(key="T-002"))
    return path


class Session:
    """A stand-in for the interactive claude session: records the call, then writes files."""

    def __init__(self, repo: Path, writes: dict[str, str] | None = None, code: int = 0):
        self.repo, self.writes, self.code, self.calls = repo, writes or {}, code, []

    def __call__(self, prompt: str, opening: str, repo: Path) -> int:
        self.calls.append((prompt, opening, repo))
        for name, text in self.writes.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return self.code


def answer(value: bool):
    asked = []

    def confirm(question: str) -> bool:
        asked.append(question)
        return value

    confirm.asked = asked
    return confirm


def go(repo, target="T-002", session=None, confirm=None, **kwargs) -> Outcome:
    return refine(
        repo,
        target,
        launcher=session or Session(repo),
        confirm=confirm or answer(True),
        interactive=kwargs.pop("interactive", True),
        **kwargs,
    )


def git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


def status_cell(repo: Path, key: str) -> str:
    line = next(ln for ln in board_text(repo).splitlines() if ln.startswith(f"| {key} "))
    return [c.strip() for c in line.strip("|").split("|")][3]


# --- the happy path -----------------------------------------------------------------------------


def test_a_valid_brief_and_a_yes_moves_the_draft_to_backlog(repo):
    session = Session(repo, {BRIEF: brief_text("T-002")})
    confirm = answer(True)
    before = board_text(repo)
    outcome = go(repo, session=session, confirm=confirm)
    assert (outcome.key, outcome.promoted, outcome.state) == ("T-002", True, "BACKLOG")
    assert status_cell(repo, "T-002") == "BACKLOG"
    assert board_text(repo) == before.replace(
        "| T-002 | T (Area) | Title of T-002 | DRAFT |",
        "| T-002 | T (Area) | Title of T-002 | BACKLOG |",
    )
    assert len(confirm.asked) == 1 and "T-002" in confirm.asked[0] and "BACKLOG" in confirm.asked[0]


def test_a_no_leaves_the_item_a_draft(repo):
    session = Session(repo, {BRIEF: brief_text("T-002")})
    before = board_text(repo)
    outcome = go(repo, session=session, confirm=answer(False))
    assert (outcome.promoted, outcome.state) == (False, "DRAFT")
    assert board_text(repo) == before


def test_the_summary_shows_what_will_be_promoted(repo, capsys):
    text = brief_text("T-002", deps="[T-001]", priority=4)
    go(repo, session=Session(repo, {BRIEF: text}))
    out = capsys.readouterr().out
    assert "T-002" in out and "priority 4" in out and "T-001" in out and "1 acceptance" in out


def test_a_backlog_item_stays_backlog_without_asking(repo):
    path = make_repo(repo.parent / "b", [("T-002", "Open")], {"T-002": brief_text("T-002")})
    confirm = answer(True)
    outcome = go(
        path, session=Session(path, {BRIEF: brief_text("T-002", priority=9)}), confirm=confirm
    )
    assert (outcome.promoted, outcome.state) == (False, "BACKLOG")
    assert confirm.asked == []
    assert "priority: 9" in (path / BRIEF).read_text()


# --- what the session is told -------------------------------------------------------------------


def test_the_prompt_lists_the_context_to_read_before_the_rules(repo):
    (repo / "CLAUDE.md").write_text("rules")
    (repo / "GLOSSARY.md").write_text("terms")
    (repo / "adr").mkdir()
    (repo / "adr" / "0001-x.md").write_text("adr")
    (repo / "docs" / "work-items" / "T-001.md").write_text(brief_text("T-001"))
    (repo / BRIEF).write_text(
        SKELETON.format(key="T-002").replace("depends_on: []", "depends_on: [T-001]")
    )
    session = Session(repo, {BRIEF: brief_text("T-002")})
    go(repo, session=session)
    prompt, opening, where = session.calls[0]
    assert where == repo and "T-002" in opening
    first, rules = prompt.index("Read first"), prompt.index("Rules for this session")
    assert first < rules
    reading = prompt[first:rules]
    for name in (
        "CLAUDE.md",
        "GLOSSARY.md",
        "adr/",
        "docs/WORK-ITEMS.md",
        "docs/work-items/T-001.md",
    ):
        assert name in reading
    assert "AGENTS.md" not in reading  # absent files are skipped
    assert "never ask the person for something" in reading


def test_the_prompt_carries_the_grilling_method_and_the_write_rules(repo):
    session = Session(repo, {BRIEF: brief_text("T-002")})
    go(repo, session=session)
    prompt = session.calls[0][0]
    assert "Method for this stage: frame" in prompt and "Interview the person" in prompt
    assert BRIEF in prompt and PROPOSALS in prompt
    assert "numbered" in prompt and "recommended answer" in prompt
    assert "Never create" in prompt  # ADRs and glossary entries are only proposed
    assert "Do not edit the board" in prompt


def test_a_changed_grilling_skill_is_refused_before_anything_starts(repo, tmp_path):
    skills = tmp_path / "skills"
    (skills / "g" / "grilling").mkdir(parents=True)
    (skills / "g" / "grilling" / "SKILL.md").write_text("tampered")
    config = {"methods": {"skills_root": str(skills), "stages": {"frame": {"sha256": "0" * 64}}}}
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump(config))
    session = Session(repo)
    with pytest.raises(CommandError, match="hash mismatch"):
        go(repo, session=session)
    assert session.calls == []


# --- refusals before the session ----------------------------------------------------------------


def test_it_refuses_without_a_terminal_and_creates_nothing(repo):
    before = board_text(repo)
    session = Session(repo)
    with pytest.raises(CommandError, match="person"):
        go(repo, "a brand new idea", session=session, interactive=False, code="T")
    assert session.calls == [] and board_text(repo) == before


@pytest.mark.parametrize("state", ["DONE", "IN_PROGRESS", "IN_REVIEW", "NEEDS_HUMAN", "FAILED"])
def test_only_draft_and_backlog_items_can_be_refined(tmp_path, state):
    path = make_repo(tmp_path, [("T-002", state)], {"T-002": brief_text("T-002")})
    session = Session(path)
    with pytest.raises(CommandError, match=state):
        go(path, session=session)
    assert session.calls == []


def test_an_unknown_key_is_refused(repo):
    with pytest.raises(CommandError, match="T-404"):
        go(repo, "T-404")


# --- starting from an idea ----------------------------------------------------------------------


def test_an_idea_becomes_a_draft_first(repo):
    new_brief = "docs/work-items/T-003.md"
    session = Session(repo, {new_brief: brief_text("T-003")})
    outcome = go(repo, "Add the thing", session=session, code="T")
    assert outcome.key == "T-003" and outcome.promoted
    assert "| T-003 | T (Area) | Add the thing | BACKLOG |" in board_text(repo)
    assert "T-003" in session.calls[0][1]


def test_an_idea_uses_the_configured_code(repo):
    (repo / ".adlc" / "config.yaml").write_text("board: {code: T}\n")
    outcome = go(
        repo, "Another", session=Session(repo, {"docs/work-items/T-003.md": brief_text("T-003")})
    )
    assert outcome.key == "T-003"


def test_an_idea_without_any_code_is_refused(repo):
    with pytest.raises(CommandError, match="--code"):
        go(repo, "An idea with nowhere to go")


def test_a_new_code_needs_the_flag(repo):
    with pytest.raises(CommandError, match="--new-code"):
        go(repo, "Fresh", code="ZZ")
    outcome = go(
        repo,
        "Fresh",
        code="ZZ",
        new_code=True,
        session=Session(repo, {"docs/work-items/ZZ-001.md": brief_text("ZZ-001")}),
    )
    assert outcome.key == "ZZ-001"


# --- the gates after the session ----------------------------------------------------------------


def test_a_brief_that_fails_the_validator_blocks_promotion(repo):
    confirm = answer(True)
    before = board_text(repo)
    with pytest.raises(CommandError, match="acceptance criteria"):
        go(repo, session=Session(repo), confirm=confirm)  # the skeleton is still unfinished
    assert confirm.asked == [] and board_text(repo) == before


def test_a_missing_brief_blocks_promotion(repo):
    (repo / BRIEF).unlink()
    with pytest.raises(CommandError, match="no brief was written"):
        go(repo)


def test_a_brief_with_the_wrong_key_blocks_promotion(repo):
    with pytest.raises(CommandError, match="T-009"):
        go(repo, session=Session(repo, {BRIEF: brief_text("T-009")}))


def test_a_dependency_on_an_unknown_item_blocks_promotion(repo):
    with pytest.raises(CommandError, match="T-099"):
        go(repo, session=Session(repo, {BRIEF: brief_text("T-002", deps="[T-099]")}))
    assert status_cell(repo, "T-002") == "DRAFT"


def test_a_session_that_ends_badly_is_not_checked(repo):
    confirm = answer(True)
    with pytest.raises(CommandError, match="exit 3"):
        go(repo, session=Session(repo, {BRIEF: brief_text("T-002")}, code=3), confirm=confirm)
    assert confirm.asked == [] and status_cell(repo, "T-002") == "DRAFT"


def test_proposals_are_allowed_and_never_applied(repo):
    (repo / "GLOSSARY.md").write_text("terms")
    session = Session(repo, {BRIEF: brief_text("T-002"), PROPOSALS: "Proposed ADR: use X"})
    git_init(repo)
    outcome = go(repo, session=session)
    assert outcome.promoted and (repo / PROPOSALS).read_text() == "Proposed ADR: use X"
    assert (repo / "GLOSSARY.md").read_text() == "terms"
    assert outcome.proposals == PROPOSALS


@pytest.mark.parametrize(
    "stray", ["GLOSSARY.md", "docs/WORK-ITEMS.md", "src/other.py", "adr/0002-new.md"]
)
def test_an_edit_outside_the_two_allowed_files_blocks_promotion(repo, stray):
    git_init(repo)
    (repo / "GLOSSARY.md").write_text("terms")
    (repo / "docs" / "WORK-ITEMS.md").write_text(board_text(repo))
    before = board_text(repo)
    confirm = answer(True)
    writes = {BRIEF: brief_text("T-002"), stray: "changed by the session"}
    with pytest.raises(CommandError, match=stray):
        go(repo, session=Session(repo, writes), confirm=confirm)
    assert confirm.asked == []
    if stray != "docs/WORK-ITEMS.md":
        assert (
            board_text(repo) == before and (repo / stray).read_text() == "changed by the session"
        )  # nothing reverted


def test_a_file_that_was_already_modified_is_caught_when_edited_again(repo):
    git_init(repo)
    (repo / "GLOSSARY.md").write_text("first")
    writes = {BRIEF: brief_text("T-002"), "GLOSSARY.md": "second"}
    with pytest.raises(CommandError, match="GLOSSARY.md"):
        go(repo, session=Session(repo, writes))


def test_a_file_that_was_already_modified_and_left_alone_is_fine(repo):
    git_init(repo)
    (repo / "GLOSSARY.md").write_text("mine")
    assert go(repo, session=Session(repo, {BRIEF: brief_text("T-002")})).promoted


def test_outside_a_git_repo_the_check_is_skipped_with_a_notice(repo, capsys):
    writes = {BRIEF: brief_text("T-002"), "stray.txt": "x"}
    assert go(repo, session=Session(repo, writes)).promoted
    assert "not a git repository" in capsys.readouterr().out


# --- the real launcher --------------------------------------------------------------------------


def test_the_default_launcher_runs_claude_with_the_prompt_file_and_cleans_up(repo, monkeypatch):
    from adlc import refine as module

    seen = {}

    def fake_run(command, cwd=None, **kwargs):
        seen["command"], seen["cwd"] = command, cwd
        seen["text"] = Path(command[command.index("--append-system-prompt-file") + 1]).read_text()
        seen["file"] = Path(command[command.index("--append-system-prompt-file") + 1])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.shutil, "which", lambda name: "claude-bin")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    code = module.claude_launcher("THE PROMPT", "Refine T-002", repo)
    assert code == 0 and seen["cwd"] == repo
    assert seen["command"][0] == "claude-bin" and seen["command"][-1] == "Refine T-002"
    assert seen["text"] == "THE PROMPT" and not seen["file"].exists()


def test_a_missing_claude_executable_is_a_clear_error(repo, monkeypatch):
    from adlc import refine as module

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(CommandError, match="claude"):
        module.claude_launcher("p", "o", repo)


# --- the command line ---------------------------------------------------------------------------


def run_cli(capsys, *argv):
    from adlc.cli import main

    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def terminal(monkeypatch, repo):
    """A person at a terminal: the session writes a good brief and the person answers y."""
    from adlc import refine as module

    session = Session(repo, {BRIEF: brief_text("T-002")})
    monkeypatch.setattr(module, "claude_launcher", session)
    monkeypatch.setattr(module, "is_interactive", lambda: True)
    monkeypatch.setattr(module, "ask", lambda question: True)
    return session


def test_cli_refine_promotes_and_exits_0(repo, capsys, terminal):
    code, out, err = run_cli(capsys, "refine", "T-002", "--repo", str(repo))
    assert (code, err) == (0, "") and "T-002 is now BACKLOG" in out
    assert status_cell(repo, "T-002") == "BACKLOG"


def test_cli_refine_an_idea_with_a_code(repo, capsys, terminal, monkeypatch):
    terminal.writes = {"docs/work-items/T-003.md": brief_text("T-003")}
    code, out, _ = run_cli(capsys, "refine", "Make it faster", "--code", "T", "--repo", str(repo))
    assert code == 0 and "added T-003 as a DRAFT" in out


def test_cli_refine_without_a_terminal_exits_1(repo, capsys, monkeypatch):
    from adlc import refine as module

    monkeypatch.setattr(module, "is_interactive", lambda: False)
    code, out, err = run_cli(capsys, "refine", "T-002", "--repo", str(repo))
    assert (code, out) == (1, "") and err.startswith("adlc: ") and "person" in err


def test_cli_refine_with_an_invalid_brief_exits_1(repo, capsys, terminal):
    terminal.writes = {}
    code, _, err = run_cli(capsys, "refine", "T-002", "--repo", str(repo))
    assert code == 1 and "not ready" in err and status_cell(repo, "T-002") == "DRAFT"


def test_cli_refine_with_a_bad_config_exits_2(repo, capsys, terminal):
    (repo / ".adlc" / "config.yaml").write_text("limit: 1\n")
    code, _, err = run_cli(capsys, "refine", "T-002", "--repo", str(repo))
    assert code == 2 and "limit: unknown key" in err
