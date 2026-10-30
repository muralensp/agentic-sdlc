"""Safe defaults and `adlc trust`: auto-merge is earned, never assumed (ADLC-026, FR-41)."""

import json
from pathlib import Path

import pytest
import yaml

from adlc.cli import main
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.escalation import approve
from adlc.fake_forge import FakeForge
from adlc.pr import Check
from adlc.scaffold import create_repo
from adlc.trust import enable, hand_merges, record_hand_merge, status
from test_commands import make_repo
from test_pr import PASS, make_scenario, run


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def write_config(repo: Path, text: str) -> None:
    (repo / ".adlc").mkdir(exist_ok=True)
    (repo / ".adlc" / "config.yaml").write_text(text, encoding="utf-8", newline="")


def config_bytes(repo: Path) -> bytes:
    return (repo / ".adlc" / "config.yaml").read_bytes()


def write_baseline(repo: Path, **statuses: str) -> None:
    """A saved baseline in which each named DoD command ended as given (pass, fail, ...)."""
    folder = repo / ".adlc" / "logs" / "baseline"
    folder.mkdir(parents=True, exist_ok=True)
    commands = [
        {
            "name": name,
            "command": f"run {name}",
            "status": result,
            "exit_code": 0 if result == "pass" else 1,
            "duration": 1.0,
            "attempts": 1,
            "flaky": False,
            "out": "",
            "err": "",
        }
        for name, result in statuses.items()
    ]
    data = {
        "schema": 1,
        "taken_at": "2026-10-30T00:00:00+00:00",
        "commit": "abc",
        "dirty": False,
        "platform": "test",
        "commands": commands,
    }
    (folder / "baseline.json").write_text(json.dumps(data), encoding="utf-8")


def merges(repo: Path, count: int) -> None:
    for n in range(count):
        record_hand_merge(repo, f"T-{n + 1:03d}", n + 1, f"https://example.test/pull/{n + 1}")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path, [("T-001", "Open")])


# --- the evidence -----------------------------------------------------------------------------


def test_a_repo_with_no_baseline_and_no_merges_is_not_ready(repo):
    got = status(repo)
    assert not got.ready
    assert got.merges == 0 and got.needed == 3 and got.auto_merge is False
    text = " ".join(got.reasons)
    assert "baseline" in text and "0 of 3" in text


def test_a_failing_baseline_names_what_fails(repo):
    write_baseline(repo, lint="pass", test="fail")
    merges(repo, 3)
    got = status(repo)
    assert not got.ready
    assert got.failing == ("test",)
    assert "test" in " ".join(got.reasons)


def test_a_skipped_command_does_not_make_the_baseline_red(repo):
    write_baseline(repo, lint="pass", smoke="skipped")
    merges(repo, 3)
    assert status(repo).ready


def test_a_green_baseline_without_enough_merges_is_not_ready(repo):
    write_baseline(repo, lint="pass", test="pass")
    merges(repo, 2)
    got = status(repo)
    assert not got.ready and "2 of 3" in " ".join(got.reasons)


def test_a_green_baseline_and_three_hand_merges_is_ready(repo):
    write_baseline(repo, lint="pass", test="pass")
    merges(repo, 3)
    got = status(repo)
    assert got.ready and got.reasons == ()


def test_the_number_of_merges_is_a_setting(repo):
    write_config(repo, "merge:\n  trust_after: 1\n")
    write_baseline(repo, lint="pass")
    merges(repo, 1)
    assert status(repo).ready


def test_a_merge_is_counted_once_however_often_it_is_recorded(repo):
    for _ in range(3):
        record_hand_merge(repo, "T-001", 1, "https://example.test/pull/1")
    assert hand_merges(repo) == ["T-001"]


# --- the PR step keeps the count ----------------------------------------------------------------


def test_a_merge_a_person_approved_is_counted(tmp_path):
    sc = make_scenario(tmp_path, merge={"auto_merge": False})
    run(sc)
    approve(sc.repo, "T-001")
    outcome, _, _ = run(sc, FakeForge([[Check("ci", PASS)]]))
    assert outcome.status == "merged"
    assert hand_merges(sc.repo) == ["T-001"]


def test_a_merge_nobody_approved_is_not_counted(tmp_path):
    sc = make_scenario(tmp_path)  # auto_merge is on in this scenario
    outcome, _, _ = run(sc)
    assert outcome.status == "merged"
    assert hand_merges(sc.repo) == []


def test_an_escalated_pull_request_is_not_counted(tmp_path):
    sc = make_scenario(tmp_path, merge={"auto_merge": False})
    outcome, _, _ = run(sc)
    assert outcome.status == "escalated"
    assert hand_merges(sc.repo) == []


# --- turning it on --------------------------------------------------------------------------------


def ready_repo(repo: Path) -> Path:
    write_baseline(repo, lint="pass", test="pass")
    merges(repo, 3)
    return repo


def test_trust_refuses_until_it_is_earned_and_changes_nothing(repo):
    write_config(repo, "# mine\nmerge:\n  auto_merge: false\n")
    before = config_bytes(repo)
    with pytest.raises(CommandError, match="baseline"):
        enable(repo)
    assert config_bytes(repo) == before


def test_trust_turns_an_existing_false_into_true_and_keeps_everything_else(repo):
    ready_repo(repo)
    write_config(
        repo, "# mine\nextends: python\nmerge:\n  auto_merge: false  # not yet\n  method: rebase\n"
    )
    enable(repo)
    text = config_bytes(repo).decode()
    assert "# mine" in text and "extends: python" in text and "method: rebase" in text
    assert "auto_merge: true" in text and "# not yet" in text
    settings = load_config(repo)
    assert settings.merge.auto_merge is True and settings.merge.method == "rebase"


def test_trust_adds_the_key_to_a_merge_block_without_it(repo):
    ready_repo(repo)
    write_config(repo, "merge:\n  method: rebase\n")
    enable(repo)
    assert load_config(repo).merge.auto_merge is True


def test_trust_adds_a_merge_block_when_there_is_none(repo):
    ready_repo(repo)
    write_config(repo, "limits:\n  max_iterations: 4\n")
    enable(repo)
    settings = load_config(repo)
    assert settings.merge.auto_merge is True and settings.limits.max_iterations == 4


def test_trust_creates_the_config_if_the_repo_has_none(repo):
    ready_repo(repo)
    assert not (repo / ".adlc" / "config.yaml").exists()
    enable(repo)
    assert load_config(repo).merge.auto_merge is True


def test_trust_will_not_guess_at_a_flow_style_block(repo):
    ready_repo(repo)
    write_config(repo, "merge: {auto_merge: false}\n")
    before = config_bytes(repo)
    with pytest.raises(CommandError, match="by hand"):
        enable(repo)
    assert config_bytes(repo) == before


def test_trust_keeps_windows_line_endings(repo):
    ready_repo(repo)
    write_config(repo, "merge:\r\n  auto_merge: false\r\n")
    enable(repo)
    assert config_bytes(repo) == b"merge:\r\n  auto_merge: true\r\n"


# --- it never flips itself ----------------------------------------------------------------------


def test_nothing_but_trust_ever_changes_the_setting(tmp_path):
    sc = make_scenario(tmp_path, merge={"auto_merge": False})
    write_baseline(sc.repo, lint="pass", test="pass")
    before = config_bytes(sc.repo)
    run(sc)
    approve(sc.repo, "T-001")
    run(sc, FakeForge([[Check("ci", PASS)]]))
    merges(sc.repo, 5)
    got = status(sc.repo)
    assert got.ready  # every condition holds ...
    assert config_bytes(sc.repo) == before  # ... and still nothing flipped
    assert load_config(sc.repo).merge.auto_merge is False


# --- the command line ---------------------------------------------------------------------------


def test_trust_command_explains_what_is_missing(repo, capsys):
    assert main(["trust", "--repo", str(repo)]) == 1
    out = capsys.readouterr()
    assert "baseline" in out.out + out.err and "0 of 3" in out.out + out.err


def test_trust_command_without_yes_changes_nothing(repo, capsys):
    ready_repo(repo)
    assert main(["trust", "--repo", str(repo)]) == 0
    assert "--yes" in capsys.readouterr().out
    assert load_config(repo).merge.auto_merge is False


def test_trust_command_with_yes_turns_it_on(repo, capsys):
    ready_repo(repo)
    assert main(["trust", "--yes", "--repo", str(repo)]) == 0
    assert load_config(repo).merge.auto_merge is True
    assert "auto_merge" in capsys.readouterr().out


# --- the defaults for a repository with no profile match ---

EXPECTED = (
    ".github/**",
    ".adlc/**",
    "**/pyproject.toml",
    "**/package.json",
    "**/requirements*.txt",
    "**/*.csproj",
    "**/go.mod",
    "**/Cargo.toml",
    "**/pom.xml",
    "**/Gemfile",
    "**/migrations/**",
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/secrets*",
)


def test_a_repo_with_no_profile_gets_the_default_protected_areas(repo):
    protected = load_config(repo).protected
    for glob in EXPECTED:
        assert glob in protected, glob


def test_a_repo_with_no_profile_does_not_auto_merge(repo):
    assert load_config(repo).merge.auto_merge is False


@pytest.mark.parametrize("stack", ["python", "dotnet"])
def test_a_new_repo_keeps_the_defaults_and_does_not_auto_merge(tmp_path, stack):
    create_repo(tmp_path / "demo", "demo", stack, "DEMO")
    settings = load_config(tmp_path / "demo")
    assert settings.merge.auto_merge is False
    for glob in EXPECTED:
        assert glob in settings.protected, glob
    assert yaml.safe_load((tmp_path / "demo" / ".adlc" / "config.yaml").read_text())["extends"]
