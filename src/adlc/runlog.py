"""The per-run log folder and its readable summaries (FR-18, FR-9h).

A run lives in `.adlc/logs/<KEY>/run-<n>/`. The loop writes `loop.json` (the measured record),
`summary.md` (what `adlc log` prints), the prompts, replies and diffs of each iteration, and one
`verify-<n>/` folder of command output per check. Times come from clocks and tokens from the
runner's usage figures, never from what an agent says; this module only reads and formats them.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adlc.commands import CommandError, latest_run

RECORD = "loop.json"
SUMMARY = "summary.md"


def new_run(repo: Path, key: str) -> tuple[int, Path]:
    """Create the folder for the next run of `key` and return its number and path."""
    latest = latest_run(repo, key)
    number = latest[0] + 1 if latest else 1
    folder = Path(repo) / ".adlc" / "logs" / key / f"run-{number}"
    folder.mkdir(parents=True)
    return number, folder


def load_record(folder: Path) -> dict[str, Any]:
    path = Path(folder) / RECORD
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError(f"the loop record {path} is missing or damaged ({exc})") from exc
    if not isinstance(data, dict):
        raise CommandError(f"the loop record {path} is damaged (not an object)")
    return data


def _count(iterations: list[Mapping[str, Any]]) -> str:
    fixes = sum(1 for i in iterations if i.get("kind", "fix") == "fix")
    diagnoses = len(iterations) - fixes
    text = f"{len(iterations)} ({fixes} fix"
    return text + (f", {diagnoses} diagnosis)" if diagnoses else ")")


def _tokens(usage: Mapping[str, Any]) -> str:
    cache = f" (cache: {usage.get('cache_read_tokens', 0):,} read, " + (
        f"{usage.get('cache_creation_tokens', 0):,} written)"
    )
    return (
        f"{usage.get('input_tokens', 0):,} in, {usage.get('output_tokens', 0):,} out{cache}; "
        f"about ${usage.get('cost_usd_list', 0):.2f} at list price"
    )


def _stopped(record: Mapping[str, Any]) -> str:
    stop = record.get("stop")
    if not stop:
        return "running"
    detail = record.get("detail")
    return f"{stop}: {detail}" if detail else str(stop)


def render_status(record: Mapping[str, Any], number: int | None = None) -> str:
    """The `adlc loop-status` view: iteration count, usage so far, last failure and stop reason."""
    iterations = list(record.get("iterations") or [])
    name = record.get("key") or "loop"
    head = f"{name} run {number}" if number else str(name)
    state = "running" if not record.get("stop") else "stopped"
    lines = [
        f"{head}: {state}",
        f"  stop reason: {_stopped(record)}",
        f"  iterations: {_count(iterations)}; review rounds: {record.get('review_rounds', 0)}",
        f"  tokens: {_tokens(record.get('usage') or {})}",
        f"  elapsed: {record.get('elapsed_seconds', 0):.1f} s",
        f"  last failure: {record.get('last_signature') or '(none)'}",
    ]
    if record.get("resets_at"):
        lines.append(f"  resumes after: {record['resets_at']}")
    return "\n".join(lines) + "\n"


def render_summary(record: Mapping[str, Any]) -> str:
    """The run summary written beside the record; `adlc log` prints it."""
    iterations = list(record.get("iterations") or [])
    title = record.get("key") or "Run"
    lines = [
        f"# {title}",
        "",
        f"- Result: {_stopped(record)}",
        f"- Iterations: {_count(iterations)}; review rounds: {record.get('review_rounds', 0)}",
        f"- Tokens: {_tokens(record.get('usage') or {})}",
        f"- Elapsed: {record.get('elapsed_seconds', 0):.1f} s (measured by the orchestrator)",
        f"- Last failure: {record.get('last_signature') or '(none)'}",
    ]
    methods = record.get("methods") or {}
    if methods:
        lines += ["- Methods:"]
        for stage, used in methods.items():
            skill = used.get("skill", "?") if isinstance(used, Mapping) else used
            source = used.get("source", "?") if isinstance(used, Mapping) else ""
            lines.append(f"  - {stage}: {skill} ({source})")
    lines += [
        "",
        "## Iterations",
        "",
        "| # | Kind | Agent | Seconds | Tokens in/out | Reverted | Files changed | Failing |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i in iterations:
        files = ", ".join(i.get("changed") or []) or "-"
        failing = ", ".join(i.get("failing") or []) or "-"
        lines.append(
            f"| {i.get('number')} | {i.get('kind', 'fix')} | {i.get('status')} "
            f"| {i.get('duration', 0):.1f} "
            f"| {i.get('input_tokens', 0)}/{i.get('output_tokens', 0)} "
            f"| {'yes' if i.get('reverted') else 'no'} | {files} | {failing} |"
        )
    if record.get("diagnosis"):
        lines += ["", "## Diagnosis", "", str(record["diagnosis"])]
    if record.get("findings"):
        lines += ["", "## Reviewer findings", "", str(record["findings"])]
    lines += [
        "",
        "## Files",
        "",
        "`prompts/`, `responses/` and `diffs/` hold one file per iteration; `verify-<n>/` holds "
        "each check's command, exit code and output (`dod.json`); `loop.json` is the full record.",
    ]
    return "\n".join(lines) + "\n"


def loop_status(repo: Path, key: str, run: int | None = None) -> str:
    """The status of the newest run of `key`, or of run number `run`."""
    if run is None:
        latest = latest_run(repo, key)
        if latest is None:
            return f"no runs yet for {key}\n"
        run, folder = latest
    else:
        folder = Path(repo) / ".adlc" / "logs" / key / f"run-{run}"
        if not folder.is_dir():
            raise CommandError(f"no run {run} for {key}")
    return render_status(load_record(folder), run)
