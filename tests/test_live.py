"""Tests that call the real `claude`. Skipped unless ADLC_LIVE=1; each run costs a few cents.

The fakes in the other tests cannot show what Claude Code does with a permission in headless mode,
which is how ADLC-035 went unseen until the first real run.
"""

import os
import subprocess

import pytest

from adlc.config import load_config
from adlc.guardrails import GuardedRunner
from adlc.runner import AgentRequest, ClaudeCodeRunner, Role

pytestmark = pytest.mark.skipif(
    os.environ.get("ADLC_LIVE") != "1", reason="calls the real claude; set ADLC_LIVE=1"
)


@pytest.mark.parametrize("role", [Role.TESTER, Role.CODER])
def test_a_headless_agent_can_create_a_file_in_its_worktree(tmp_path, role):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    settings = load_config(tmp_path)
    runner = GuardedRunner(ClaudeCodeRunner(settings.models), settings)
    result = runner.run(
        AgentRequest(
            role, "Create a file named probe.txt containing the word hi, then stop.", tmp_path
        )
    )
    assert result.status == "ok", result.detail
    assert result.permission_denials == ()
    assert (tmp_path / "probe.txt").is_file()
