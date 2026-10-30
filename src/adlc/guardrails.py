"""Rules the orchestrator enforces around every agent run (FR-15, FR-16, FR-17).

The model is never trusted to obey them. Before a call, `GuardedRunner` scrubs the prompt and hands
Claude Code a deny list for network tools; after it, the log and the reply are scrubbed. After every
iteration the loop re-checks what the agent changed and the shell commands it ran
(`command_violations`) and stops if either broke the rules. This is a layer, not a guarantee:
pattern-based scrubbing misses what it has no pattern for, and there is no operating-system
sandbox, which is why the protected-area escalation exists as well.
"""

import dataclasses
import re
import shlex
from collections.abc import Callable, Sequence
from pathlib import Path

from adlc.config import Guardrails, Settings
from adlc.redact import redact, redact_pans
from adlc.runner import AgentRequest, AgentResult, AgentRunner

WEB_TOOLS = ("WebFetch", "WebSearch")
_SEPARATORS = re.compile(r"&&|\|\||[;|&\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# --- scrubbing ----------------------------------------------------------------------------------


def make_scrubber(settings: Settings) -> Callable[[str], str]:
    """A function that removes secrets, card numbers and the configured patterns from text."""
    extra = [re.compile(p) for p in settings.scrub.patterns]

    def scrub(text: str) -> str:
        text = redact(text)
        if settings.scrub.pan:
            text = redact_pans(text)
        for pattern in extra:
            text = pattern.sub("[REDACTED]", text)
        return text

    return scrub


# --- commands -----------------------------------------------------------------------------------


def split_commands(line: str) -> list[list[str]]:
    """The simple commands in a shell line, each as its words, without leading VAR=value."""
    found = []
    for piece in _SEPARATORS.split(line):
        try:
            words = shlex.split(piece, posix=True)
        except ValueError:
            words = piece.split()
        while words and _ASSIGNMENT.match(words[0]):
            words = words[1:]
        if words:
            words[0] = re.split(r"[\\/]", words[0])[-1]  # /usr/bin/curl -> curl
            found.append(words)
    return found


def _starts_with(words: Sequence[str], rule: str) -> bool:
    parts = rule.split()
    return list(words[: len(parts)]) == parts


def command_violations(commands: Sequence[str], guard: Guardrails) -> list[str]:
    """Why each shell line breaks the rules: a forbidden command, or one off the allow-list."""
    found = []
    for line in commands:
        for words in split_commands(line):
            shown = " ".join(words)
            if any(_starts_with(words, rule) for rule in guard.forbidden_commands):
                found.append(f"forbidden command `{shown}` (in `{line}`)")
            elif guard.allowed_commands and not any(
                _starts_with(words, rule) for rule in guard.allowed_commands
            ):
                found.append(f"command `{shown}` is not on the allow-list (in `{line}`)")
    return found


def deny_rules(guard: Guardrails) -> tuple[str, ...]:
    """The tools Claude Code is told never to use: web access and every forbidden command."""
    return (*WEB_TOOLS, *(f"Bash({rule}:*)" for rule in guard.forbidden_commands))


# --- the runner ---------------------------------------------------------------------------------


class GuardedRunner:
    """Wraps any runner: scrubbed prompts, a deny list on every call, scrubbed logs and replies."""

    def __init__(self, inner: AgentRunner, settings: Settings):
        self.inner = inner
        self.guardrails = settings.guardrails
        self.scrub = make_scrubber(settings)

    def run(self, request: AgentRequest) -> AgentResult:
        denied = tuple(dict.fromkeys((*request.disallowed_tools, *deny_rules(self.guardrails))))
        guarded = dataclasses.replace(
            request,
            prompt=self.scrub(request.prompt),
            method=self.scrub(request.method) if request.method else request.method,
            disallowed_tools=denied,
        )
        result = self.inner.run(guarded)
        if request.log_path and Path(request.log_path).is_file():
            raw = Path(request.log_path).read_bytes().decode("utf-8", errors="replace")
            Path(request.log_path).write_bytes(self.scrub(raw).encode("utf-8"))
        return dataclasses.replace(
            result, text=self.scrub(result.text), detail=self.scrub(result.detail)
        )
