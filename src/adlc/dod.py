"""Run Definition of Done commands and record what really happened (FR-10a).

Each command runs as `bash -c` in the repo, with its output redirected to files and no pipe, so the
exit code is the process's own. A failed command is run once more to expose a flaky start; a command
that hangs is killed with everything it started and is not retried.
"""

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from adlc.commands import CommandError

DEFAULT_TIMEOUT_SECONDS = 20 * 60
_BASH_NEXT_TO_GIT = ("bin/bash.exe", "usr/bin/bash.exe")


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: str
    status: str  # pass, fail, timeout or skipped
    exit_code: int | None
    duration: float  # seconds, all attempts together
    attempts: int
    flaky: bool  # failed first, passed on the retry
    out: str  # file names inside the output folder; the last attempt's
    err: str

    @property
    def failed(self) -> bool:
        return self.status in ("fail", "timeout")


def find_bash(*, windows=None, which=shutil.which, environ=os.environ) -> str:
    """The bash to run commands with: ADLC_BASH, else Git for Windows' own, never WSL's."""
    windows = os.name == "nt" if windows is None else windows
    chosen = environ.get("ADLC_BASH")
    if chosen:
        if not Path(chosen).is_file():
            raise CommandError(f"ADLC_BASH points to {chosen}, which is not a file")
        return chosen
    if windows:
        # `bash` on the Windows PATH is usually WSL's, which would run commands inside Linux.
        git = which("git")
        for parent in Path(git).parents[:3] if git else ():
            for relative in _BASH_NEXT_TO_GIT:
                if (parent / relative).is_file():
                    return str(parent / relative)
        raise CommandError(
            "Git Bash was not found next to git; install Git for Windows or set ADLC_BASH "
            "to the path of its bash.exe"
        )
    found = which("bash")
    if found:
        return found
    raise CommandError("bash was not found on PATH; install it or set ADLC_BASH")


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.kill()


def _once(bash: str, command: str, cwd: Path, out: Path, err: Path, timeout: float):
    """One attempt: (exit code or None on timeout, seconds)."""
    extra = {} if os.name == "nt" else {"start_new_session": True}
    started = time.monotonic()
    with open(out, "wb") as stdout, open(err, "wb") as stderr:
        process = subprocess.Popen(
            [bash, "-c", command],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            **extra,
        )
        try:
            code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            process.wait()
            code = None
    return code, time.monotonic() - started


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_.-" else "_" for c in name)


def run_command(
    name: str, command: str, cwd: Path, out_dir: Path, timeout: float, bash: str
) -> CommandResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe(name)
    out, err = out_dir / f"{stem}.out", out_dir / f"{stem}.err"
    code, seconds = _once(bash, command, cwd, out, err, timeout)
    attempts, flaky = 1, False
    if code not in (0, None):  # a failure, not a hang: look once more for a flaky start
        os.replace(out, out_dir / f"{stem}.attempt1.out")
        os.replace(err, out_dir / f"{stem}.attempt1.err")
        code, again = _once(bash, command, cwd, out, err, timeout)
        attempts, seconds, flaky = 2, seconds + again, code == 0
    status = "pass" if code == 0 else "timeout" if code is None else "fail"
    return CommandResult(name, command, status, code, seconds, attempts, flaky, out.name, err.name)


def run_all(
    dod: Mapping[str, str],
    cwd: Path,
    out_dir: Path,
    *,
    bash: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    only: Sequence[str] = (),
    skip: Sequence[str] = (),
    progress: Callable[[str], None] | None = None,
) -> list[CommandResult]:
    """Run the commands in order; a failure does not stop the rest. Unselected ones are skipped."""
    if not dod:
        raise CommandError(
            "no Definition of Done commands are configured; extend a stack profile "
            "(extends: dotnet or python) or add a `dod:` block to .adlc/config.yaml"
        )
    unknown = [n for n in (*only, *skip) if n not in dod]
    if unknown:
        raise CommandError(f"unknown command name {', '.join(unknown)} (known: {', '.join(dod)})")
    results = []
    for name, command in dod.items():
        if (only and name not in only) or name in skip:
            results.append(CommandResult(name, command, "skipped", None, 0, 0, False, "", ""))
            continue
        if progress:
            progress(name)
        results.append(run_command(name, command, cwd, out_dir, timeout, bash))
    return results
