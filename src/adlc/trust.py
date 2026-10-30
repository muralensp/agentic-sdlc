"""Earning auto-merge: `adlc trust` is the only thing that turns it on (FR-41, decisions 18, 20).

A fresh project has no CI history and few tests, so `merge.auto_merge` stays off. It may be turned
on once the doctor baseline is green and a person has approved `merge.trust_after` merges by hand
(three by default). Nothing here, or anywhere else, flips the setting by itself: `status` only
reads, and `enable` runs only when a person asks, and only when the evidence is in.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adlc.baseline import load_baseline
from adlc.commands import CommandError
from adlc.config import ConfigError, load_config

LEDGER = Path(".adlc") / "trust.json"
CONFIG = Path(".adlc") / "config.yaml"
_MERGE_BLOCK = re.compile(r"^merge:[ \t]*(#.*)?$")
_AUTO_MERGE = re.compile(r"^([ \t]+auto_merge:[ \t]*)[^\s#]+")


@dataclass(frozen=True)
class Trust:
    baseline: str  # green, failing, empty, missing or damaged
    failing: tuple[str, ...]
    merges: int  # merges a person approved by hand
    needed: int
    auto_merge: bool

    @property
    def reasons(self) -> tuple[str, ...]:
        found = []
        if self.baseline == "failing":
            found.append(f"the doctor baseline is not green; failing: {', '.join(self.failing)}")
        elif self.baseline != "green":
            found.append(
                f"the doctor baseline is {self.baseline}; run `adlc doctor --baseline` and fix it"
            )
        if self.merges < self.needed:
            found.append(f"{self.merges} of {self.needed} merges approved by hand")
        return tuple(found)

    @property
    def ready(self) -> bool:
        return not self.reasons


# --- the count ----------------------------------------------------------------------------------


def _read(repo: Path) -> dict:
    path = Path(repo) / LEDGER
    if not path.is_file():
        return {"merges": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("merges"), dict):
            raise ValueError("no merges table")
    except (OSError, ValueError, AttributeError) as exc:
        raise CommandError(f"{path} is damaged ({exc}); fix or delete it") from exc
    return data


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".trust.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_hand_merge(
    repo: Path, key: str, number: int, url: str, now: datetime | None = None
) -> None:
    """Note that a person approved the merge of `key`. A task counts once."""
    data = _read(repo)
    data["merges"].setdefault(
        key, {"pr": number, "url": url, "at": (now or datetime.now(UTC)).isoformat()}
    )
    _replace(Path(repo) / LEDGER, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def hand_merges(repo: Path) -> list[str]:
    """The tasks whose merge a person approved, oldest first."""
    merges = _read(repo)["merges"]
    return sorted(merges, key=lambda key: merges[key]["at"])


# --- the evidence -------------------------------------------------------------------------------


def status(repo: Path) -> Trust:
    """Whether auto-merge has been earned. Reads only."""
    repo = Path(repo)
    settings = load_config(repo)
    try:
        baseline = load_baseline(repo)
    except CommandError:
        state, failing = "damaged", ()
    else:
        if baseline is None:
            state, failing = "missing", ()
        else:
            ran = [c for c in baseline.commands if c.status != "skipped"]
            failing = tuple(c.name for c in ran if c.failed)
            state = "failing" if failing else "green" if ran else "empty"
    return Trust(
        state,
        failing,
        len(hand_merges(repo)),
        settings.merge.trust_after,
        settings.merge.auto_merge,
    )


# --- turning it on ------------------------------------------------------------------------------


def _with_auto_merge(text: str) -> str:
    """`text` with `merge.auto_merge: true`, changing nothing else. Block style only."""
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    start = next(
        (i for i, line in enumerate(lines) if _MERGE_BLOCK.match(line.rstrip("\r\n"))), None
    )
    if start is None:
        if any(line.startswith("merge:") for line in lines):
            raise CommandError(
                "the config writes `merge:` on one line (flow style); set auto_merge: true by hand"
            )
        gap = "" if not text or text.endswith("\n") else eol
        return f"{text}{gap}merge:{eol}  auto_merge: true{eol}"
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
        end += 1
    for i in range(start + 1, end):
        changed = _AUTO_MERGE.sub(r"\1true", lines[i])
        if changed != lines[i]:
            lines[i] = changed
            return "".join(lines)
    found = (re.match(r"[ \t]+", line) for line in lines[start + 1 : end])
    indent = next((m.group(0) for m in found if m), "  ")
    lines.insert(start + 1, f"{indent}auto_merge: true{eol}")
    return "".join(lines)


def enable(repo: Path) -> Trust:
    """Turn `merge.auto_merge` on in `.adlc/config.yaml`, if it has been earned."""
    repo = Path(repo)
    before = status(repo)
    if before.auto_merge:
        raise CommandError("auto_merge is already on")
    if not before.ready:
        raise CommandError("auto_merge is not earned yet: " + "; ".join(before.reasons))
    path = repo / CONFIG
    original = path.read_bytes() if path.is_file() else None
    text = original.decode("utf-8") if original is not None else ""
    _replace(path, _with_auto_merge(text).encode("utf-8"))
    try:
        turned_on = load_config(repo).merge.auto_merge
    except ConfigError:
        turned_on = False
    if not turned_on:  # the edit did not take, or broke the file: put it back
        if original is None:
            path.unlink(missing_ok=True)
        else:
            _replace(path, original)
        raise CommandError("could not turn auto_merge on in the config; edit it by hand")
    return status(repo)
