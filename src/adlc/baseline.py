"""The baseline: what a repo's Definition of Done commands do before any task touches it (FR-29).

A task run is compared with it afterwards: a failure that was already there does not block the
task, a new one does. The files live in `.adlc/logs/baseline/`, which is local to a checkout.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from adlc.commands import CommandError
from adlc.config import Settings
from adlc.dod import DEFAULT_TIMEOUT_SECONDS, CommandResult, find_bash, run_all

SCHEMA = 1
STORE = Path(".adlc") / "logs" / "baseline"
_FIELDS = tuple(CommandResult.__dataclass_fields__)


@dataclass(frozen=True)
class Baseline:
    schema: int
    taken_at: str
    commit: str | None
    dirty: bool | None
    platform: str
    commands: tuple[CommandResult, ...]


@dataclass(frozen=True)
class Verdict:
    name: str
    verdict: str  # pass, pre-existing, new, fixed or skipped
    status: str  # how the command ended in this run
    baseline: str | None  # how it ended in the baseline, None if it was not there


def _git_state(repo: Path) -> tuple[str | None, bool | None]:
    """The commit and whether tracked files have uncommitted changes; (None, None) outside git."""

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8"
        )

    try:
        head = git("rev-parse", "HEAD")
        status = git("status", "--porcelain", "--untracked-files=no")
    except OSError:
        return None, None
    if head.returncode != 0 or status.returncode != 0:
        return None, None
    return head.stdout.strip(), bool(status.stdout.strip())


def _write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".baseline.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def take_baseline(
    repo: Path,
    settings: Settings,
    *,
    bash: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    only: Sequence[str] = (),
    skip: Sequence[str] = (),
    allow_dirty: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Baseline:
    """Run the profile's commands once on the repo as it is, and save the result."""
    commit, dirty = _git_state(repo)
    if dirty and not allow_dirty:
        raise CommandError(
            "the working tree has uncommitted changes, so this would not be a baseline of the "
            "unmodified repo; commit or stash them, or pass --allow-dirty"
        )
    folder = repo / STORE
    results = run_all(
        settings.dod,
        repo,
        folder,
        bash=bash or find_bash(),
        timeout=timeout,
        only=only,
        skip=skip,
        progress=progress,
    )
    folder.mkdir(parents=True, exist_ok=True)
    keep = {"baseline.json"}
    for result in results:
        if result.out:
            stem = result.out.removesuffix(".out")
            keep |= {
                f"{stem}{part}.{kind}" for part in ("", ".attempt1") for kind in ("out", "err")
            }
    for old in folder.iterdir():
        if old.suffix in (".out", ".err") and old.name not in keep:
            old.unlink()
    taken = Baseline(
        SCHEMA,
        datetime.now(UTC).isoformat(timespec="seconds"),
        commit,
        dirty,
        sys.platform,
        tuple(results),
    )
    _write(folder / "baseline.json", json.dumps(asdict(taken), indent=2) + "\n")
    return taken


def load_baseline(repo: Path) -> Baseline | None:
    """The saved baseline, or None if there is none."""
    path = repo / STORE / "baseline.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
        if data["schema"] != SCHEMA:
            raise ValueError(f"schema {data['schema']} is not {SCHEMA}")
        commands = tuple(CommandResult(**{f: c[f] for f in _FIELDS}) for c in data["commands"])
        return Baseline(
            data["schema"],
            data["taken_at"],
            data["commit"],
            data["dirty"],
            data["platform"],
            commands,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandError(
            f"the baseline file {path} is damaged or from another version ({exc}); take a new one"
        ) from exc


def compare(results: Sequence[CommandResult], baseline: Baseline | None) -> list[Verdict]:
    """Judge each result against the baseline; a failure it did not have before is `new`."""
    before = {c.name: c for c in baseline.commands} if baseline else {}
    verdicts = []
    for result in results:
        prior = before.get(result.name)
        prior_status = prior.status if prior else None
        if result.status == "skipped":
            verdict = "skipped"
        elif not result.failed:
            verdict = "fixed" if prior is not None and prior.failed else "pass"
        else:
            verdict = "pre-existing" if prior is not None and prior.failed else "new"
        verdicts.append(Verdict(result.name, verdict, result.status, prior_status))
    return verdicts


def blocked(verdicts: Sequence[Verdict]) -> bool:
    """A task is blocked only by failures that are new."""
    return any(v.verdict == "new" for v in verdicts)
