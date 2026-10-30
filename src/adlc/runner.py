"""One interface for calling an agent with a role, a model and instructions (FR-22).

`ClaudeCodeRunner` makes each call a fresh headless `claude -p` process: no session is resumed, so
nothing carries over except what is in files and git. The caller decides what the work was worth;
the runner only reports what happened. It never raises for a bad run, only for a request that must
not be made (a reviewer on the coder's model) or a machine that cannot make it (no `claude`).
"""

import dataclasses
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from adlc.commands import CommandError
from adlc.config import Models
from adlc.dod import _kill_tree

DEFAULT_TIMEOUT_SECONDS = 30 * 60
_LIMIT_TEXT = re.compile(r"hit your limit|usage limit reached|rate.?limit", re.IGNORECASE)
_STDERR_TAIL = 2000


class Role(StrEnum):
    """The four judgment jobs; the values are the keys under `models:` in the config."""

    TESTER = "tester"
    CODER = "coder"
    REVIEWER = "reviewer"
    PR_WRITER = "pr_writer"


AGENT_NAMES = {
    Role.TESTER: "tester",
    Role.CODER: "coder",
    Role.REVIEWER: "reviewer",
    Role.PR_WRITER: "pr-writer",
}  # the subagent files `adlc` writes to .claude/agents/ (ADLC-033)


@dataclass(frozen=True)
class AgentRequest:
    role: Role
    prompt: str
    cwd: Path
    method: str | None = None  # instructions added to the system prompt (from adlc.stages)
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    agent: str | None = None  # a Claude Code subagent definition (ADLC-033)
    log_path: Path | None = None  # where to save the raw stream


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd_list: float = 0.0  # list price: an estimate, not a bill, on a subscription


@dataclass(frozen=True)
class RateLimit:
    status: str  # rejected, allowed_warning, allowed ...
    limit_type: str | None = None  # five_hour or seven_day
    resets_at: datetime | None = None  # UTC
    using_overage: bool = False


@dataclass(frozen=True)
class AgentResult:
    status: str  # ok, error, timeout or rate_limited
    text: str = ""
    detail: str = ""  # why it was not ok
    usage: Usage = field(default_factory=Usage)
    models: Mapping[str, float] = field(default_factory=dict)  # model id -> list-price cost
    primary_model: str | None = None  # the model that cost most
    turns: int = 0
    permission_denials: tuple[Any, ...] = ()
    duration: float = 0.0  # seconds, measured by the runner
    exit_code: int | None = None
    rate_limit: RateLimit | None = None
    commands: tuple[str, ...] = ()  # the shell commands the agent ran, from its stream
    raw: Mapping[str, Any] = field(default_factory=dict)  # the final result event


class AgentRunner(Protocol):
    def run(self, request: AgentRequest) -> AgentResult: ...


def same_model(a: str | None, b: str | None) -> bool:
    """True if two model names are the same model; an alias counts as its full id."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a == b or a in b or b in a


def check_role_model(models: Models, role: Role) -> str | None:
    """The model for a role; the reviewer must have one and it must differ from the coder's."""
    model = getattr(models, role.value)
    if role is Role.REVIEWER:
        if not model:
            raise CommandError(
                "models.reviewer is not set; the reviewer must name a model that differs "
                "from models.coder"
            )
        if same_model(model, models.coder):
            raise CommandError(
                f"models.reviewer ({model}) is the same model as models.coder "
                f"({models.coder}); the reviewer must use a different model"
            )
    return model


def _when(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def rate_limit_from(info: Mapping[str, Any]) -> RateLimit:
    return RateLimit(
        status=str(info.get("status", "")),
        limit_type=info.get("rateLimitType"),
        resets_at=_when(info.get("resetsAt")),
        using_overage=bool(info.get("isUsingOverage", False)),
    )


def _events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


_CREDENTIAL_NAME = re.compile(
    r"(?i)token|secret|passw|credential|api[_-]?key|private[_-]?key|^(aws|azure|gh|github)_"
)
_KEEP_ENV = re.compile(r"^(ANTHROPIC|CLAUDE)_")  # what Claude Code needs to log in


def agent_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment an agent gets: no credentials but Claude Code's own (FR-17)."""
    return {
        name: value
        for name, value in environ.items()
        if _KEEP_ENV.match(name) or not _CREDENTIAL_NAME.search(name)
    }


def commands_from(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The shell commands an agent ran, in order, read from its `stream-json` events."""
    found = []
    for event in events:
        message = event.get("message") if event.get("type") == "assistant" else None
        for block in (message or {}).get("content") or ():
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "Bash"
            ):
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str):
                    found.append(command)
    return tuple(found)


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class ClaudeCodeRunner:
    def __init__(
        self,
        models: Models,
        *,
        command: Sequence[str] = ("claude",),
        max_cost_usd: float | None = None,
        rate_limit_log: Path | None = None,
    ):
        self.models = models
        self.command = tuple(command)
        self.max_cost_usd = max_cost_usd
        self.rate_limit_log = rate_limit_log

    def _arguments(self, request: AgentRequest, model: str | None, method_file: str | None):
        args = [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--setting-sources",
            "project",
            "--no-session-persistence",
        ]
        if model:
            args += ["--model", model]
        if method_file:
            args += ["--append-system-prompt-file", method_file]
        agent = request.agent
        if agent is None:
            known = Path(request.cwd) / ".claude" / "agents" / f"{AGENT_NAMES[request.role]}.md"
            agent = AGENT_NAMES[request.role] if known.is_file() else None
        if agent:
            args += ["--agent", agent]
        if request.allowed_tools:
            args += ["--allowedTools", ",".join(request.allowed_tools)]
        if request.disallowed_tools:
            args += ["--disallowedTools", ",".join(request.disallowed_tools)]
        if self.max_cost_usd is not None:
            args += ["--max-budget-usd", f"{self.max_cost_usd:g}"]
        return args

    def run(self, request: AgentRequest) -> AgentResult:
        model = check_role_model(self.models, request.role)
        method_file = None
        if request.method:
            handle = tempfile.NamedTemporaryFile(
                "wb", suffix=".md", prefix="adlc-method-", delete=False
            )
            with handle:
                handle.write(request.method.encode("utf-8"))
            method_file = handle.name
        try:
            return self._call(request, model, method_file)
        finally:
            if method_file:
                Path(method_file).unlink(missing_ok=True)

    def _call(self, request: AgentRequest, model: str | None, method_file: str | None):
        argv = [*self.command, *self._arguments(request, model, method_file)]
        extra = {} if os.name == "nt" else {"start_new_session": True}
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=request.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=agent_env(os.environ),
                **extra,
            )
        except OSError as exc:
            raise CommandError(
                f"could not start claude ({self.command[0]}): {exc}; install Claude Code "
                "or put it on PATH"
            ) from exc
        timed_out = False
        try:
            out, err = process.communicate(request.prompt.encode("utf-8"), request.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process)
            out, err = process.communicate()
        duration = time.monotonic() - started
        stdout, stderr = _text(out), _text(err)
        if request.log_path:
            request.log_path.parent.mkdir(parents=True, exist_ok=True)
            request.log_path.write_bytes(out)
        events = _events(stdout)
        result = self._judge(
            request, events, stdout, stderr, process.returncode, timed_out, duration
        )
        result = dataclasses.replace(result, commands=commands_from(events))
        if result.status == "rate_limited":
            self._keep_sample(request, events, stdout, stderr)
        return result

    def _judge(self, request, events, stdout, stderr, code, timed_out, duration) -> AgentResult:
        final = next((e for e in reversed(events) if e.get("type") == "result"), None)
        infos = [
            e["rate_limit_info"]
            for e in events
            if e.get("type") == "rate_limit_event" and isinstance(e.get("rate_limit_info"), dict)
        ]
        base = {"duration": duration, "exit_code": None if timed_out else code}
        if timed_out:
            return AgentResult(
                "timeout", detail=f"no result within {request.timeout:g} seconds", **base
            )

        rejected = next((i for i in infos if i.get("status") == "rejected"), None)
        text = str(final.get("result", "")) if final else ""
        errored = bool(final and final.get("is_error"))
        limited_by_status = bool(final and errored and final.get("api_error_status") == 429)
        limited_by_text = (errored and bool(_LIMIT_TEXT.search(text))) or (
            final is None and bool(_LIMIT_TEXT.search(stderr))
        )
        if rejected or limited_by_status or limited_by_text:
            limit = rate_limit_from(rejected) if rejected else RateLimit(status="rejected")
            return self._finish("rate_limited", final, text or stderr[-_STDERR_TAIL:], limit, base)

        limit = rate_limit_from(infos[-1]) if infos else None
        if final is None:
            detail = f"no result from claude (exit {code}): {stderr[-_STDERR_TAIL:].strip()}"
            return AgentResult("error", detail=detail, rate_limit=limit, **base)
        if code != 0:
            detail = f"claude exited with {code}: {stderr[-_STDERR_TAIL:].strip()}"
            return self._finish("error", final, detail, limit, base)
        reason = final.get("terminal_reason")
        if (
            errored
            or final.get("subtype") not in (None, "success")
            or reason not in (None, "completed")
        ):
            detail = f"{final.get('subtype')}/{reason}: {text}".strip()
            return self._finish("error", final, detail, limit, base)
        return self._finish("ok", final, "", limit, base)

    @staticmethod
    def _finish(status, final, detail, limit, base) -> AgentResult:
        final = final or {}
        usage = final.get("usage") or {}
        by_model = final.get("modelUsage") or {}
        costs = {
            name: float(v.get("costUSD", 0) or 0)
            for name, v in by_model.items()
            if isinstance(v, dict)
        }
        primary = max(costs, key=lambda n: costs[n]) if costs else None
        return AgentResult(
            status,
            text=str(final.get("result", "")) if status == "ok" else "",
            detail=detail,
            usage=Usage(
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
                int(usage.get("cache_read_input_tokens", 0) or 0),
                int(usage.get("cache_creation_input_tokens", 0) or 0),
                float(final.get("total_cost_usd", 0) or 0),
            ),
            models=costs,
            primary_model=primary,
            turns=int(final.get("num_turns", 0) or 0),
            permission_denials=tuple(final.get("permission_denials") or ()),
            rate_limit=limit,
            raw=final,
            **base,
        )

    def _keep_sample(self, request, events, stdout, stderr) -> None:
        """Save what a usage limit looked like, so the classifier can be checked against reality."""
        if not self.rate_limit_log:
            return
        row = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "role": request.role.value,
            "events": events,
            "stderr": stderr[-_STDERR_TAIL:],
            "stdout_non_json": [ln for ln in stdout.splitlines() if ln and not ln.startswith("{")],
        }
        self.rate_limit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.rate_limit_log, "ab") as handle:
            handle.write((json.dumps(row) + "\n").encode("utf-8"))
