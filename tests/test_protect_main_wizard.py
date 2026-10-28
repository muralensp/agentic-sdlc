"""The protect-main wizard, run against a stub `gh` and a local bare repo as `origin`."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".adlc" / "wizards" / "protect-main.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

STUB_GH = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
case "$*" in
  "auth status") exit 0 ;;
  "api user --jq .login") echo tester ;;
  "repo view "*) [ "${STUB_REPO_EXISTS:-1}" = 1 ] ;;
  "repo create "*) ;;
  "api -X PUT "*) cat > /dev/null ;;
  *"--jq .enforce_admins.enabled") echo "${STUB_ADMINS:-true}" ;;
  *"--jq .required_status_checks.contexts[]") echo "CI passed" ;;
  *"--jq .allow_force_pushes.enabled") echo false ;;
  *"--jq .required_pull_request_reviews|type") echo object ;;
  *) echo "stub: unexpected call: $*" >&2; exit 9 ;;
esac
"""

REJECT_HOOK = """#!/bin/sh
echo "remote: error: GH006: Protected branch update failed for refs/heads/main." >&2
exit 1
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def env(tmp_path: Path):
    """A working repo on main with a bare `origin` that already holds main."""
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    work.mkdir()
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("a\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "origin", "main")
    stub = tmp_path / "gh"
    stub.write_text(STUB_GH, newline="\n")
    log = tmp_path / "gh.log"
    return work, bare, stub, log


def run(env, *args: str, **extra: str):
    work, _bare, stub, log = env
    path = str(Path(BASH).parent) + os.pathsep + os.environ["PATH"]
    environ = {**os.environ, "PATH": path, "ADLC_GH": str(stub), "STUB_LOG": str(log), **extra}
    return subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=work, env=environ, capture_output=True, text=True
    )


def reject_pushes_to_main(bare: Path) -> None:
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(REJECT_HOOK, newline="\n")
    hook.chmod(0o755)


def mutating_calls(log: Path) -> list[str]:
    lines = log.read_text().splitlines() if log.exists() else []
    return [ln for ln in lines if ln.startswith(("repo create", "api -X PUT"))]


def test_script_is_valid_bash():
    done = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_dry_run_changes_nothing(env):
    done = run(env, "--dry-run", STUB_REPO_EXISTS="0")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "will do: gh repo create tester/work --public" in done.stdout
    assert mutating_calls(env[3]) == []


def test_rejected_push_is_shown_as_evidence(env):
    reject_pushes_to_main(env[1])
    done = run(env, "--yes")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "rejected, as required" in done.stdout
    assert "GH006" in done.stdout


def test_accepted_direct_push_fails_the_wizard(env):
    """Fault injection: if origin does not block the push, the wizard must say so and exit 1."""
    done = run(env, "--yes")
    assert done.returncode == 1
    assert "ACCEPTED" in done.stdout


def test_unenforced_admins_skip_the_probe(env):
    done = run(env, "--yes", STUB_ADMINS="false")
    assert "FAIL admins not enforced" in done.stdout
    assert "skipped: protection is not verified" in done.stdout


def test_rerun_creates_nothing_when_repo_exists(env):
    reject_pushes_to_main(env[1])
    run(env, "--yes")
    run(env, "--yes")
    assert not any(c.startswith("repo create") for c in mutating_calls(env[3]))


def test_requires_gh_login(env):
    stub = env[2]
    stub.write_text("#!/usr/bin/env bash\nexit 1\n", newline="\n")
    done = run(env, "--yes")
    assert done.returncode == 1
    assert "gh auth login" in done.stderr
