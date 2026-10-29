"""Test-first rules the driver enforces around the Tester and the Coder (FR-8, FR-9g).

Three checks on a task's worktree, each returning a plain result for the loop controller to act on:
what the Tester changed (it may touch only tests), whether the new tests really fail on the
unmodified code (`red_check`), and whether the Coder left the Tester's tests alone (`lock_tests`,
`check_locked`). The lock lives outside the worktree, so the Coder cannot reach it.
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from adlc.commands import CommandError
from adlc.config import Settings
from adlc.dod import DEFAULT_TIMEOUT_SECONDS, find_bash, run_command
from adlc.scope import out_of_scope

SCHEMA = 1
RED_RECORD = "red.json"
LOCK_RECORD = "lock.json"
WHOLE_RUN = "<all tests>"
NO_DETAILS = "(no details given)"
_PROPOSAL = re.compile(r"^#{1,6}[ \t]*proposed test change[ \t]*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}[ \t]")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tdd.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def _git(worktree: Path, *args: str) -> bytes:
    done = subprocess.run(["git", *args], cwd=worktree, capture_output=True, check=False)
    if done.returncode != 0:
        raise CommandError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace')}")
    return done.stdout


# --- what changed -------------------------------------------------------------------------------


def changed_paths(worktree: Path, base: str) -> dict[str, str]:
    """Paths changed in the worktree since `base`, as {path: A, M or D}; untracked files are A."""
    changes: dict[str, str] = {}
    parts = _git(worktree, "diff", "--name-status", "--no-renames", "-z", base).decode().split("\0")
    for status, path in zip(parts[0::2], parts[1::2], strict=False):
        changes[path] = status[:1] if status[:1] in ("A", "D") else "M"
    others = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").decode()
    for path in filter(None, others.split("\0")):
        changes.setdefault(path, "A")
    return changes


def out_of_scope_for_tester(changes: dict[str, str], settings: Settings) -> list[str]:
    """Changed paths the Tester may not touch: anything that is not a test, or is protected."""
    return out_of_scope(list(changes), settings.protected, allowed=settings.tests.paths)


# --- the red check ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RedFile:
    path: str  # the test file, or WHOLE_RUN
    verdict: str  # red (fails for a real reason), passes, or broken (build error, hang, no marker)
    exit_code: int | None
    duration: float
    attempts: int
    out: str  # file names inside the output folder
    err: str


@dataclass(frozen=True)
class RedCheck:
    files: tuple[RedFile, ...]
    record: Path

    @property
    def ok(self) -> bool:
        """Every new test fails for a real reason."""
        return bool(self.files) and all(f.verdict == "red" for f in self.files)

    @property
    def passing(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.verdict == "passes")

    @property
    def broken(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files if f.verdict == "broken")


def _prints_a_failure(out_dir: Path, result, markers: Sequence[str]) -> bool:
    text = ""
    for name in (result.out, result.err):
        text += (out_dir / name).read_bytes().decode("utf-8", errors="replace") + "\n"
    return any(m.lower() in text.lower() for m in markers)


def red_check(
    worktree: Path,
    settings: Settings,
    out_dir: Path,
    files: Sequence[str],
    *,
    bash: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RedCheck:
    """Run the new tests on the unmodified code; each must fail for a real reason."""
    tests = settings.tests
    if not tests.paths:
        raise CommandError(
            "no test file locations are configured; set tests.paths in .adlc/config.yaml "
            "or extend a stack profile (extends: dotnet or python)"
        )
    if not files:
        raise CommandError("there are no new or changed test files to check")
    if tests.red:
        runs = [
            (f, tests.red.replace("{file}", shlex.quote(f.replace(chr(92), "/")))) for f in files
        ]
    elif "test" in settings.dod:
        runs = [(WHOLE_RUN, settings.dod["test"])]
    else:
        raise CommandError("tests.red is not set and the Definition of Done has no `test` command")
    bash = bash or find_bash()
    out_dir = Path(out_dir)
    checked = []
    for path, command in runs:
        # retry_under=0: a test that fails is the expected outcome, so it is never run twice
        done = run_command(path, command, Path(worktree), out_dir, timeout, bash, retry_under=0)
        if done.status == "pass":
            verdict = "passes"
        elif done.status == "fail" and _prints_a_failure(out_dir, done, tests.failure_markers):
            verdict = "red"
        else:
            verdict = "broken"
        checked.append(
            RedFile(path, verdict, done.exit_code, done.duration, done.attempts, done.out, done.err)
        )
    result = RedCheck(tuple(checked), out_dir / RED_RECORD)
    document = {"schema": SCHEMA, "ok": result.ok, "files": [vars(f) for f in checked]}
    _write(result.record, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
    return result


# --- the lock -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    path: str
    kind: str  # modified or deleted


@dataclass(frozen=True)
class Lock:
    folder: Path
    files: dict[str, str]  # path -> sha256 of its bytes

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(self.files)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        raise CommandError(f"{rel} is outside the worktree")
    return target


def lock_tests(worktree: Path, files: Sequence[str], lock_dir: Path) -> Lock:
    """Remember the Tester's test files (a hash and a copy of each) so they can be checked later."""
    lock_dir = Path(lock_dir)
    hashes: dict[str, str] = {}
    for rel in files:
        path = _inside(Path(worktree), rel)
        if not path.is_file():
            raise CommandError(f"cannot lock {rel}: it is not a file")
        data = path.read_bytes()
        _write(lock_dir / "files" / rel, data)
        hashes[rel] = _sha(data)
    document = {"schema": SCHEMA, "files": hashes}
    _write(lock_dir / LOCK_RECORD, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
    return Lock(lock_dir, hashes)


def load_lock(lock_dir: Path) -> Lock:
    record = Path(lock_dir) / LOCK_RECORD
    try:
        data = json.loads(record.read_bytes().decode("utf-8"))
        files = {str(k): str(v) for k, v in data["files"].items()}
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise CommandError(f"the test lock {record} is missing or damaged ({exc})") from exc
    return Lock(Path(lock_dir), files)


def check_locked(worktree: Path, lock: Lock) -> list[Violation]:
    """The locked tests that were changed or removed, in lock order."""
    found = []
    for rel, digest in lock.files.items():
        path = _inside(Path(worktree), rel)
        if not path.is_file():
            found.append(Violation(rel, "deleted"))
        elif _sha(path.read_bytes()) != digest:
            found.append(Violation(rel, "modified"))
    return found


def restore_locked(worktree: Path, lock: Lock, violations: Sequence[Violation]) -> None:
    """Put the saved copy of each violated test back; nothing else is touched."""
    for violation in violations:
        if violation.path not in lock.files:
            continue
        target = _inside(Path(worktree), violation.path)
        _write(target, (lock.folder / "files" / violation.path).read_bytes())


# --- proposals ----------------------------------------------------------------------------------


def proposed_test_change(progress: str) -> str | None:
    """The Coder's proposal to change a test, if `progress.md` has the heading for one."""
    lines = progress.splitlines()
    for i, line in enumerate(lines):
        if _PROPOSAL.match(line.strip()):
            body = []
            for later in lines[i + 1 :]:
                if _HEADING.match(later):
                    break
                body.append(later)
            return "\n".join(body).strip() or NO_DETAILS
    return None
