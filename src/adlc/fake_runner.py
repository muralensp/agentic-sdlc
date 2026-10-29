"""A scripted stand-in for the agent runner, so the orchestrator can be tested with no process or
network (ADLC-012). Give it results, or functions that return results (and may edit files).
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from adlc.config import Models
from adlc.runner import AgentRequest, AgentResult, RateLimit, check_role_model

Step = AgentResult | Callable[[AgentRequest], AgentResult]


def ok(text: str = "OK", **kwargs) -> AgentResult:
    return AgentResult("ok", text=text, **kwargs)


def error(detail: str = "failed", **kwargs) -> AgentResult:
    return AgentResult("error", detail=detail, **kwargs)


def timeout(**kwargs) -> AgentResult:
    return AgentResult("timeout", detail="timed out", **kwargs)


def rate_limited(resets_at: datetime | float | None = None, limit_type: str = "five_hour"):
    if isinstance(resets_at, (int, float)):
        resets_at = datetime.fromtimestamp(resets_at, UTC)
    limit = RateLimit("rejected", limit_type, resets_at)
    return AgentResult("rate_limited", detail="usage limit reached", rate_limit=limit)


class FakeRunner:
    def __init__(self, script: Sequence[Step], *, models: Models | None = None):
        self._script = list(script)
        self._models = models
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResult:
        if self._models is not None:
            check_role_model(self._models, request.role)
        self.requests.append(request)
        if not self._script:
            raise AssertionError(
                f"the FakeRunner script ran out at call {len(self.requests)} ({request.role})"
            )
        step = self._script.pop(0)
        return step(request) if callable(step) else step
