"""A scripted stand-in for the code host, so the PR step can be tested with no network (ADLC-021).

Give it the check results to return, one list per poll; the last list repeats.
"""

from collections.abc import Sequence
from pathlib import Path

from adlc.pr import Check, PullRequest


class FakeForge:
    def __init__(self, checks: Sequence[Sequence[Check]] = ((),), *, number: int = 7):
        self._checks = [list(c) for c in checks]
        self._number = number
        self.calls: list[tuple] = []

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def push(self, worktree: Path, branch: str) -> None:
        self.calls.append(("push", branch))

    def create_pr(
        self, worktree: Path, *, base: str, branch: str, title: str, body: str
    ) -> PullRequest:
        self.calls.append(("create_pr", base, branch, title, body))
        return PullRequest(self._number, f"https://example.test/pull/{self._number}", branch)

    def checks(self, pr: PullRequest) -> list[Check]:
        self.calls.append(("checks", pr.number))
        return self._checks.pop(0) if len(self._checks) > 1 else list(self._checks[0])

    def mark_needs_human(self, pr: PullRequest) -> None:
        self.calls.append(("mark_needs_human", pr.number))

    def merge(self, pr: PullRequest, method: str) -> None:
        self.calls.append(("merge", pr.number, method))

    def delete_remote_branch(self, repo: Path, branch: str) -> None:
        self.calls.append(("delete_remote_branch", branch))
