"""Verify a task's worktree: run the Definition of Done and keep the evidence (FR-10, FR-10a).

The commands come from settings loaded from the primary checkout, never from the worktree, so an
agent cannot change what "done" means. Each command is run by `adlc.dod` (no pipe, real exit code,
output saved) and judged against the baseline (`adlc.baseline`). A task is `ready` only after a
full pass with no new failure; a pre-existing failure is reported but does not block. A command the
baseline recorded as skipped ("this machine cannot run it") is not run and is listed as not run,
unless it is named explicitly.
"""

import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from adlc.baseline import Baseline, Verdict, blocked, compare
from adlc.commands import CommandError
from adlc.config import Settings, missing_dod
from adlc.dod import DEFAULT_TIMEOUT_SECONDS, CommandResult, find_bash, run_all

SCHEMA = 1
RECORD = "dod.json"
DEFAULT_REPORT_CHARS = 6000


@dataclass(frozen=True)
class Verification:
    results: tuple[CommandResult, ...]
    verdicts: tuple[Verdict, ...]
    baseline: Baseline | None
    duration: float  # seconds, wall clock for the whole pass
    record: Path
    not_run: tuple[str, ...] = ()  # left out because the baseline skipped them

    @property
    def complete(self) -> bool:
        """Every configured command ran, except those the baseline skipped."""
        return all(r.status != "skipped" or r.name in self.not_run for r in self.results)

    @property
    def green(self) -> bool:
        """Every command that ran exited 0."""
        return all(r.status in ("pass", "skipped") for r in self.results)

    @property
    def blocked(self) -> bool:
        """At least one failure is new, not in the baseline."""
        return blocked(self.verdicts)

    @property
    def ready(self) -> bool:
        """A full pass with no new failure: the task may be finished."""
        return self.complete and not self.blocked

    @property
    def flaky(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.results if r.flaky)

    @property
    def pre_existing(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.verdicts if v.verdict == "pre-existing")

    @property
    def skipped(self) -> tuple[str, ...]:
        """Left out by choice (a quick run), not because the baseline skipped them."""
        return tuple(
            r.name for r in self.results if r.status == "skipped" and r.name not in self.not_run
        )


def _write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dod.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def verify(
    worktree: Path,
    settings: Settings,
    out_dir: Path,
    *,
    baseline: Baseline | None = None,
    bash: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    only: Sequence[str] = (),
    progress: Callable[[str], None] | None = None,
) -> Verification:
    """Run the Definition of Done in `worktree`; save output and `dod.json` in `out_dir`."""
    missing = missing_dod(settings)
    if missing:
        raise CommandError(
            f"the Definition of Done has no {', '.join(missing)} command; "
            "extend a stack profile or add a `dod:` block to .adlc/config.yaml"
        )
    not_run = tuple(
        c.name
        for c in (baseline.commands if baseline else ())
        if c.status == "skipped" and c.name in settings.dod and c.name not in only
    )
    started = time.monotonic()
    results = run_all(
        settings.dod,
        Path(worktree),
        out_dir,
        bash=bash or find_bash(),
        timeout=timeout,
        only=only,
        skip=not_run,
        progress=progress,
    )
    duration = time.monotonic() - started
    verdicts = compare(results, baseline)
    record = Path(out_dir) / RECORD
    done = Verification(tuple(results), tuple(verdicts), baseline, duration, record, not_run)
    _write(record, json.dumps(_document(done, Path(worktree)), indent=2) + "\n")
    return done


def _document(v: Verification, worktree: Path) -> dict:
    return {
        "schema": SCHEMA,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "worktree": str(worktree),
        "baseline": (
            {"commit": v.baseline.commit, "taken_at": v.baseline.taken_at} if v.baseline else None
        ),
        "not_run": list(v.not_run),
        "complete": v.complete,
        "green": v.green,
        "blocked": v.blocked,
        "ready": v.ready,
        "duration": round(v.duration, 3),
        "commands": [
            {**asdict(r), "verdict": x.verdict, "baseline_status": x.baseline}
            for r, x in zip(v.results, v.verdicts, strict=True)
        ],
    }


def _tail(path: Path, limit: int) -> str:
    if limit <= 0 or not path.is_file():
        return ""
    text = path.read_bytes().decode("utf-8", errors="replace").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def failure_report(v: Verification, out_dir: Path, max_chars: int = DEFAULT_REPORT_CHARS) -> str:
    """Text for the next iteration: each new failure with the end of its output, within a limit."""
    new = [r for r, x in zip(v.results, v.verdicts, strict=True) if x.verdict == "new"]
    head = f"Definition of Done: {len(new)} new failure(s)." if new else "No new failures."
    feet = [
        f"{label}: {', '.join(names)}"
        for label, names in (
            ("Pre-existing failures (not blocking)", v.pre_existing),
            ("Flaky (passed on retry)", v.flaky),
            ("Not run (skipped in baseline)", v.not_run),
            ("Skipped", v.skipped),
        )
        if names
    ]

    def block(r: CommandResult, per_stream: int) -> str:
        what = "timed out" if r.status == "timeout" else f"failed (exit {r.exit_code})"
        lines = [f"### {r.name}: {what}; full output in {r.out} and {r.err}"]
        for label, name in (("stderr", r.err), ("stdout", r.out)):
            tail = _tail(Path(out_dir) / name, per_stream)
            if tail:
                lines += [f"{label} (end):", tail]
        return "\n".join(lines)

    per_stream = max_chars
    while True:
        text = "\n\n".join([head, *(block(r, per_stream) for r in new), *feet])
        if len(text) <= max_chars or per_stream <= 0:
            return text[:max_chars]
        per_stream = int(per_stream * 0.9) - 1
