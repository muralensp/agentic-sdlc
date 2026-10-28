"""PreToolUse hook: refuse file edits to the board and protected areas (ADLC-032 prototype).

Reads the hook payload on stdin. Prints a deny decision as JSON, or nothing to allow.
Protected patterns come from ADLC_PROTECTED (semicolon separated), else DEFAULT_PROTECTED.

Limit, shown by the live run in docs/research/claude-code-substrate.md: this covers the file
tools only. A shell command can still write a protected file, so the driver must re-check the diff.
"""

import fnmatch
import json
import os
import posixpath
import re
import sys

DEFAULT_PROTECTED = ("docs/WORK-ITEMS.md", ".github/*", ".adlc/*", ".claude/*")
WRITERS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _norm(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


def _relative(path: str, cwd: str) -> str:
    path, root = _norm(path), _norm(cwd or ".")
    if re.match(r"^([A-Za-z]:)?/", path):
        if path.lower().startswith(root.lower().rstrip("/") + "/"):
            return path[len(root.rstrip("/")) + 1 :]
        return path  # outside the repository
    return path


def decide(payload: dict, patterns: tuple[str, ...]) -> dict | None:
    if payload.get("tool_name") not in WRITERS:
        return None
    args = payload.get("tool_input") or {}
    target = args.get("file_path") or args.get("notebook_path")
    if not target:
        return None
    relative = _relative(target, payload.get("cwd", ""))
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"{relative} is a protected path ({pattern}). "
                        "Only the orchestrator or a person may change it."
                    ),
                }
            }
    return None


def main() -> int:
    patterns = tuple(filter(None, os.environ.get("ADLC_PROTECTED", "").split(";")))
    decision = decide(json.load(sys.stdin), patterns or DEFAULT_PROTECTED)
    if decision:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
