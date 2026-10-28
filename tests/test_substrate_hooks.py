"""The ADLC-032 prototype hooks: a PreToolUse edit guard and a Stop hook that runs the DoD."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "spikes" / "substrate" / "hooks"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("guard_edits")
stop = load("stop_dod")


def edit(path: str, tool: str = "Edit") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": path}, "cwd": "/repo"}


# --- guard_edits ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/repo/docs/WORK-ITEMS.md",
        "/repo/.github/workflows/ci.yml",
        "/repo/.adlc/config.yaml",
        "/repo/.claude/settings.json",
        "docs/WORK-ITEMS.md",
    ],
)
def test_protected_paths_are_denied(path):
    decision = guard.decide(edit(path), guard.DEFAULT_PROTECTED)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "protected" in decision["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("path", ["/repo/src/app.py", "/repo/docs/other.md", "/repo/calc.py"])
def test_other_paths_are_allowed(path):
    assert guard.decide(edit(path), guard.DEFAULT_PROTECTED) is None


@pytest.mark.parametrize("tool", ["Write", "MultiEdit"])
def test_every_file_writing_tool_is_checked(tool):
    assert guard.decide(edit("/repo/docs/WORK-ITEMS.md", tool), guard.DEFAULT_PROTECTED)


def test_reading_a_protected_file_is_allowed():
    assert guard.decide(edit("/repo/docs/WORK-ITEMS.md", "Read"), guard.DEFAULT_PROTECTED) is None


def test_dot_dot_cannot_escape_the_check():
    assert guard.decide(edit("/repo/src/../docs/WORK-ITEMS.md"), guard.DEFAULT_PROTECTED)


def test_windows_backslashes_are_normalised():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "C:\\r\\docs\\WORK-ITEMS.md"}}
    payload["cwd"] = "C:\\r"
    assert guard.decide(payload, guard.DEFAULT_PROTECTED)


# --- stop_dod -------------------------------------------------------------------------------------


def run_stop(tmp_path, command, session="s1", max_blocks=2):
    payload = {"session_id": session, "cwd": str(tmp_path), "stop_hook_active": False}
    return stop.decide(payload, command, tmp_path / "state", max_blocks)


def test_green_dod_lets_the_agent_stop(tmp_path):
    assert run_stop(tmp_path, [sys.executable, "-c", "raise SystemExit(0)"]) is None


def test_red_dod_blocks_the_stop_and_quotes_the_output(tmp_path):
    cmd = [sys.executable, "-c", "print('boom'); raise SystemExit(3)"]
    decision = run_stop(tmp_path, cmd)
    assert decision["decision"] == "block"
    assert "exit 3" in decision["reason"]
    assert "boom" in decision["reason"]


def test_blocking_stops_after_the_limit_so_the_agent_cannot_loop_forever(tmp_path):
    cmd = [sys.executable, "-c", "raise SystemExit(1)"]
    assert run_stop(tmp_path, cmd)["decision"] == "block"
    assert run_stop(tmp_path, cmd)["decision"] == "block"
    assert run_stop(tmp_path, cmd) is None  # limit of 2 reached: hand control back


def test_the_limit_is_per_session(tmp_path):
    cmd = [sys.executable, "-c", "raise SystemExit(1)"]
    run_stop(tmp_path, cmd, "a")
    run_stop(tmp_path, cmd, "a")
    assert run_stop(tmp_path, cmd, "b")["decision"] == "block"


def test_a_missing_command_blocks_with_a_clear_reason(tmp_path):
    decision = run_stop(tmp_path, ["no-such-binary-xyz"])
    assert decision["decision"] == "block"
    assert "could not run" in decision["reason"]


def test_main_prints_json_for_a_block(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ADLC_DOD", f'"{sys.executable}" -c "raise SystemExit(1)"')
    monkeypatch.setenv("ADLC_STATE_DIR", str(tmp_path / "state"))
    payload = json.dumps({"session_id": "m", "cwd": str(tmp_path)})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert stop.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
