"""The add, status and log commands through the command line (ADLC-007)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from adlc.cli import main
from test_commands import board_text, brief_text, make_repo, make_runs


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path,
        [("T-001", "DONE"), ("T-002", "Open"), ("T-003", "FAILED", "tests red")],
        {"T-002": brief_text("T-002"), "T-003": brief_text("T-003")},
    )


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_add_prints_the_new_key_and_writes_the_files(repo, capsys):
    code, out, err = run(capsys, "add", "T", "A new idea", "--repo", str(repo))
    assert (code, err) == (0, "")
    assert out.strip().splitlines()[0] == "T-004"
    assert "| T-004 | T (Area) | A new idea | DRAFT |" in board_text(repo)
    assert (repo / "docs" / "work-items" / "T-004.md").is_file()


def test_add_options_reach_the_brief(repo, capsys):
    argv = ["add", "T", "Fix", "--type", "bugfix", "--priority", "3", "--prd", "FR-5"]
    argv += ["--depends", "T-001", "--depends", "T-002", "--repo", str(repo)]
    assert run(capsys, *argv)[0] == 0
    text = (repo / "docs" / "work-items" / "T-004.md").read_text()
    assert "type: bugfix" in text and "priority: 3" in text and "[T-001, T-002]" in text
    assert "| DRAFT | FR-5 |" in board_text(repo)


def test_add_with_an_unknown_code_exits_1_and_explains(repo, capsys):
    before = board_text(repo)
    code, out, err = run(capsys, "add", "ZZ", "x", "--repo", str(repo))
    assert (code, out) == (1, "")
    assert err.startswith("adlc: ") and "--new-code" in err
    assert board_text(repo) == before


def test_add_with_new_code(repo, capsys):
    assert run(capsys, "add", "ZZ", "x", "--new-code", "--repo", str(repo))[0] == 0
    assert "| ZZ-001 | ZZ | x | DRAFT |" in board_text(repo)


def test_a_bad_type_is_a_usage_error(repo, capsys):
    with pytest.raises(SystemExit) as caught:
        main(["add", "T", "x", "--type", "epic", "--repo", str(repo)])
    assert caught.value.code == 2
    capsys.readouterr()


def test_commands_find_the_repo_from_a_subfolder(repo, capsys, monkeypatch):
    deep = repo / "src" / "pkg"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    code, out, _ = run(capsys, "status")
    assert code == 0 and "T-003" in out


def test_outside_any_repo_the_commands_stop_without_creating_anything(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr("adlc.commands.STOP", tmp_path)
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    monkeypatch.chdir(lonely)
    code, out, err = run(capsys, "add", "T", "x")
    assert (code, out) == (1, "") and "repository" in err
    assert list(lonely.iterdir()) == []


def test_an_invalid_config_exits_2_listing_the_problems(repo, capsys):
    (repo / ".adlc" / "config.yaml").write_text("limit: 1\nmerge: {auto_merge: 3}\n")
    code, out, err = run(capsys, "status", "--repo", str(repo))
    assert code == 2 and out == ""
    assert "limit: unknown key" in err and "merge.auto_merge" in err


def test_a_missing_board_exits_2(repo, capsys):
    (repo / "docs" / "WORK-ITEMS.md").unlink()
    code, _, err = run(capsys, "status", "--repo", str(repo))
    assert code == 2 and err.startswith("adlc: ") and "WORK-ITEMS.md" in err


def test_a_locked_board_exits_2_for_add(repo, capsys):
    (repo / "docs" / ".WORK-ITEMS.md.lock").write_text("42")
    code, _, err = run(capsys, "add", "T", "x", "--repo", str(repo))
    assert code == 2 and "locked" in err


def test_status_puts_failures_first_and_exits_0(repo, capsys):
    code, out, _ = run(capsys, "status", "--repo", str(repo))
    assert code == 0  # reporting failures is the job, so they do not change the exit code
    assert out.index("T-003") < out.index("T-002")
    assert "tests red" in out


def test_status_json(repo, capsys):
    code, out, _ = run(capsys, "status", "--json", "--repo", str(repo))
    data = json.loads(out)
    assert code == 0 and data["done_count"] == 1
    assert [i["key"] for i in data["items"]] == ["T-003", "T-002", "T-001"] or {
        i["key"] for i in data["items"]
    } == {"T-001", "T-002", "T-003"}
    assert next(i for i in data["items"] if i["key"] == "T-003")["group"] == "attention"


def test_status_all_includes_done(repo, capsys):
    assert "T-001" not in run(capsys, "status", "--repo", str(repo))[1]
    assert "T-001" in run(capsys, "status", "--all", "--repo", str(repo))[1]


def test_log_prints_the_newest_summary(repo, capsys):
    make_runs(repo, "T-003", {1: "first run", 2: "second run"})
    assert run(capsys, "log", "T-003", "--repo", str(repo)) == (0, "second run\n", "")
    assert run(capsys, "log", "T-003", "--run", "1", "--repo", str(repo))[1] == "first run\n"


def test_log_without_runs_exits_0_and_an_unknown_key_exits_1(repo, capsys):
    assert run(capsys, "log", "T-002", "--repo", str(repo)) == (0, "no runs yet for T-002\n", "")
    code, out, err = run(capsys, "log", "T-404", "--repo", str(repo))
    assert (code, out) == (1, "") and "T-404" in err


def test_the_module_entry_point_runs_status(repo):
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "adlc", "status", "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0 and "T-003" in done.stdout


# --- a copy of BOSS NextGen's real board (skipped unless ADLC_BOSS_BOARD is set) -----------------

BOSS = os.environ.get("ADLC_BOSS_BOARD")


@pytest.mark.skipif(not BOSS, reason="set ADLC_BOSS_BOARD to BOSS NextGen's docs/WORK-ITEMS.md")
def test_add_and_status_work_on_a_copy_of_the_boss_board(tmp_path, capsys):
    (tmp_path / ".adlc").mkdir()
    (tmp_path / "docs").mkdir()
    shutil.copyfile(BOSS, tmp_path / "docs" / "WORK-ITEMS.md")
    before = (tmp_path / "docs" / "WORK-ITEMS.md").read_bytes().decode("utf-8")
    code, out, err = run(capsys, "add", "PLAT", "Probe item", "--repo", str(tmp_path))
    assert (code, out.splitlines()[0], err) == (0, "PLAT-008", "")
    after = (tmp_path / "docs" / "WORK-ITEMS.md").read_bytes().decode("utf-8")
    old, new = before.split("\n"), after.split("\n")
    added = [line for line in new if line not in old]
    assert len(added) == 1 and added[0].startswith("| PLAT-008 | ")
    assert [line for line in new if line != added[0]] == old
    code, out, _ = run(capsys, "status", "--repo", str(tmp_path))
    assert code == 0 and "PLAT-008" in out
