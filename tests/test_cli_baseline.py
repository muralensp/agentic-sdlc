"""`adlc doctor --baseline` (ADLC-011)."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from adlc.cli import main

DOD = {"build": "echo built", "lint": "exit 3", "test": "echo ok"}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / ".adlc").mkdir(parents=True)
    (path / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": DOD}))
    return path


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["doctor", "--baseline", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def saved(repo: Path) -> dict:
    return json.loads((repo / ".adlc" / "logs" / "baseline" / "baseline.json").read_text())


def test_the_table_shows_each_command_and_a_failure_exits_1(repo, capsys):
    code, out, err = run(capsys, "--repo", str(repo))
    assert (code, err) == (1, "")
    for word in ("NAME", "STATUS", "EXIT", "build", "lint", "test", "pass", "fail"):
        assert word in out
    assert "2 of 3 passed" in out and ".adlc/logs/baseline" in out.replace(chr(92), "/")
    assert [c["status"] for c in saved(repo)["commands"]] == ["pass", "fail", "pass"]


def test_progress_is_printed_as_each_command_starts(repo, capsys):
    out = run(capsys, "--repo", str(repo))[1]
    assert out.index("running build") < out.index("running lint") < out.index("running test")


def test_an_all_green_baseline_exits_0(repo, capsys):
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": {"a": "echo a"}}))
    code, out, _ = run(capsys, "--repo", str(repo))
    assert code == 0 and "1 of 1 passed" in out


def test_only_and_skip_leave_the_others_skipped_and_do_not_fail_the_run(repo, capsys):
    code, out, _ = run(capsys, "--repo", str(repo), "--only", "build")
    assert code == 0 and out.count("skipped") >= 2
    assert [c["status"] for c in saved(repo)["commands"]] == ["pass", "skipped", "skipped"]
    code, _, _ = run(capsys, "--repo", str(repo), "--skip", "lint")
    assert code == 0


def test_the_timeout_is_in_minutes(repo, capsys):
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": {"hang": "sleep 60"}}))
    code, out, _ = run(capsys, "--repo", str(repo), "--timeout", "0.01")
    assert code == 1 and "timeout" in out
    assert saved(repo)["commands"][0]["status"] == "timeout"


def test_a_flaky_command_is_shown_as_such(repo, capsys):
    flaky = "if test -f seen; then exit 0; fi; touch seen; exit 1"
    (repo / ".adlc" / "config.yaml").write_text(yaml.safe_dump({"dod": {"f": flaky}}))
    code, out, _ = run(capsys, "--repo", str(repo))
    assert code == 0 and "flaky" in out


def test_a_dirty_tree_is_refused_with_a_hint(repo, capsys):
    (repo / "tracked.txt").write_text("one")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.t", "commit", "-q", "-m", "x"],
        cwd=repo,
        check=True,
    )
    (repo / "tracked.txt").write_text("two")
    code, out, err = run(capsys, "--repo", str(repo))
    assert (code, out) == (1, "") and err.startswith("adlc doctor: ") and "--allow-dirty" in err
    assert run(capsys, "--repo", str(repo), "--allow-dirty")[0] == 1  # lint still fails
    assert saved(repo)["dirty"] is True


def test_an_unknown_command_name_exits_1(repo, capsys):
    code, out, err = run(capsys, "--repo", str(repo), "--only", "nope")
    assert (code, out) == (1, "") and "nope" in err


def test_no_commands_exits_1_with_a_hint(repo, capsys):
    (repo / ".adlc" / "config.yaml").write_text("")
    code, _, err = run(capsys, "--repo", str(repo))
    assert code == 1 and "profile" in err


def test_a_bad_config_exits_2(repo, capsys):
    (repo / ".adlc" / "config.yaml").write_text("limit: 1\n")
    code, _, err = run(capsys, "--repo", str(repo))
    assert code == 2 and "limit: unknown key" in err


def test_plain_doctor_does_not_run_any_command(repo, capsys):
    main(["doctor", "--repo", str(repo)])
    capsys.readouterr()
    assert not (repo / ".adlc" / "logs").exists()


def test_the_timeout_option_is_converted_from_minutes_to_seconds(repo, capsys, monkeypatch):
    seen = {}

    def fake_take(repo, settings, **kwargs):
        seen.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("adlc.cli.take_baseline", fake_take)
    with pytest.raises(SystemExit):
        main(["doctor", "--baseline", "--repo", str(repo), "--timeout", "2"])
    assert seen["timeout"] == 120
    capsys.readouterr()
