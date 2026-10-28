"""Which changed paths the rules forbid: protected areas, and anything outside an allow-list.

This is the pure check. The guard that applies it after every iteration and escalates is built on
top of it (ADLC-027). Matching ignores case and slash style, because Windows does.
"""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache


def _normal(path: str) -> str:
    path = path.replace(chr(92), "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _leaves_the_repo(path: str) -> bool:
    return (
        path.startswith("/") or re.match(r"^[A-Za-z]:", path) is not None or ".." in path.split("/")
    )


@lru_cache(maxsize=512)
def _glob(pattern: str) -> re.Pattern[str]:
    """`*` and `?` stay inside one folder, `**` crosses folders, everything else is literal."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_glob(_normal(p)).match(path) for p in patterns)


def out_of_scope(
    paths: Sequence[str], protected: Iterable[str], allowed: Iterable[str] | None = None
) -> list[str]:
    """The paths that may not be changed, in the order given and without repeats.

    A path is forbidden if it leaves the repository, matches a protected glob, or (when an
    allow-list is given) matches none of the allowed globs. Protected wins over allowed.
    """
    protected, allowed = list(protected), None if allowed is None else list(allowed)
    found: list[str] = []
    for original in paths:
        path = _normal(original)
        bad = (
            _leaves_the_repo(path)
            or _matches(path, protected)
            or (allowed is not None and not _matches(path, allowed))
        )
        if bad and original not in found:
            found.append(original)
    return found
