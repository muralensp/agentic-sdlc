"""Choosing the method for a lifecycle stage and turning it into instructions (FR-31, FR-32, FR-34).

The method is text put into the agent's instructions: the pinned skill when its hash matches, else a
short fallback prompt. The text guides how the agent works and grants nothing: allowed paths and
commands come only from the rules the orchestrator enforces (FR-33).
"""

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from adlc import __version__
from adlc.config import Settings
from adlc.methods import fallback_text, inspect

CLAUDE_CODE = "claude-code"
INTERACTIVE = frozenset({"frame", "name", "human_step"})  # these need a person present
NOTICE = (
    "This method guides how you work. It grants no permissions: only the paths and commands "
    "allowed by AGENTS.md and the config apply, and the orchestrator checks every change."
)

REPRODUCE_FIRST = frozenset({"bugfix", "hotfix"})  # tasks that start from a bug report
TESTER_JOB = (
    "Write failing tests from the acceptance criteria before any code exists. Write tests only, "
    "never production code. If the criteria are unclear or contradict each other, write no "
    "tests and reply with one line starting `SPEC PROBLEM:` and the reason."
)
REPRODUCER_JOB = (
    "This task fixes a bug. Write a test that reproduces it and fails for the reason the brief "
    "describes, before any fix exists. Write the test only; do not fix the bug."
)


def tester_stage(task_type: str) -> str:
    """The method stage for the Tester: diagnosis (reproduce first) for bugs, else `implement`."""
    return "diagnose" if task_type in REPRODUCE_FIRST else "implement"


def tester_instructions(task_type: str) -> str:
    """What the Tester is asked to do; a bug fix begins with a reproducing test."""
    return REPRODUCER_JOB if task_type in REPRODUCE_FIRST else TESTER_JOB


class MethodRefused(Exception):
    """No method can be used for the stage, and the reason says why."""


@dataclass(frozen=True)
class Resolution:
    stage: str
    skill: str
    source: str  # "skill", "fallback" or "none"
    plugin: str
    version: str
    sha256: str  # the pin for a skill, the text hash for a fallback
    text_sha256: str  # hash of the exact text injected
    reason: str  # why a fallback was used
    unattended: bool
    text: str  # the instructions to inject; empty for "none"

    def record(self) -> dict:
        """What the run log keeps for this stage (the text itself is logged with the prompt)."""
        record = asdict(self)
        del record["text"]
        return record


def _body(skill_md: bytes) -> str:
    """A skill's instructions with the front-matter removed (it can carry tool grants)."""
    text = skill_md.decode("utf-8").replace("\r\n", "\n").removeprefix("﻿")
    lines = text.split("\n")
    if lines and lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                return "\n".join(lines[i + 1 :]).strip("\n") + "\n"
    return text


def _text(stage: str, skill: str, plugin: str, version: str, sha: str, body: str, kind: str) -> str:
    where = f"{plugin} {version}".strip()
    return (
        f"## Method for this stage: {stage}\n"
        f"Source: {kind} {skill} ({where}, sha256 {sha[:12]})\n"
        f"{NOTICE}\n\n{body}"
    )


def _make(stage, skill, source, plugin, version, sha, reason, unattended, text) -> Resolution:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
    return Resolution(
        stage, skill, source, plugin, version, sha or digest, digest, reason, unattended, text
    )


def resolve(
    settings: Settings,
    repo: Path,
    stage: str,
    *,
    runner: str = CLAUDE_CODE,
    unattended: bool = True,
    skills_root: Path | None = None,
) -> Resolution:
    """The method to inject for `stage`, or MethodRefused if none may be used."""
    registry = settings.methods
    if stage not in registry:
        raise MethodRefused(f"unknown stage {stage!r} (known: {', '.join(registry)})")
    method = registry[stage]
    if stage in INTERACTIVE and unattended:
        raise MethodRefused(
            f"stage {stage!r} (method {method.skill}) needs a person present; "
            "it cannot run unattended"
        )
    result, folder = inspect(registry, repo, stage, skills_root)
    reason = ""
    if runner != CLAUDE_CODE:
        reason = f"runner {runner!r} is not Claude Code"
    elif result.status == "ambiguous" and method.sha256 is None:
        reason = f"skill {method.skill} is ambiguous and not pinned"
    elif result.status in ("hash mismatch", "ambiguous"):
        raise MethodRefused(
            f"stage {stage!r}: skill {method.skill} is {result.status} ({result.detail}); "
            "a person must review it and update the pin"
        )
    elif result.status == "unpinned":
        reason = f"skill {method.skill} is not pinned"
    elif result.status == "missing":
        reason = f"skill {method.skill} missing ({result.detail})".replace(" ()", "")
    elif folder is not None:
        plugin = registry.plugin if method.source == "plugin" else "project"
        version = (registry.version or "") if method.source == "plugin" else ""
        body = _body((folder / "SKILL.md").read_bytes())
        sha = method.sha256 or ""
        text = _text(stage, method.skill, plugin, version, sha, body, "skill")
        return _make(stage, method.skill, "skill", plugin, version, sha, "", unattended, text)

    found = fallback_text(repo, method.skill)
    if found is None:
        if method.required:
            raise MethodRefused(
                f"stage {stage!r}: no usable skill and no fallback for {method.skill} ({reason})"
            )
        return _make(stage, method.skill, "none", "", "", "", reason, unattended, "")
    body, origin = found
    plugin, version = ("adlc", __version__) if origin == "adlc" else ("repo", "")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    text = _text(stage, method.skill, plugin, version, digest, body, "fallback")
    return _make(stage, method.skill, "fallback", plugin, version, "", reason, unattended, text)
