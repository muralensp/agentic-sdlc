"""Regenerate CHANGELOG.md from git history, grouped by date and Conventional Commit type.

Usage: python .claude/skills/changelog/generate.py [--output CHANGELOG.md] [--ref HEAD]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

TYPE_TITLES = {
    "feat": "Features",
    "fix": "Fixes",
    "docs": "Documentation",
    "test": "Tests",
    "refactor": "Refactoring",
    "perf": "Performance",
    "build": "Build",
    "ci": "CI",
    "chore": "Chores",
    "other": "Other",
}
CONVENTIONAL = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?: (?P<subject>.+)$"
)


def git_log(ref: str) -> list[tuple[str, str, str]]:
    out = subprocess.run(
        ["git", "log", ref, "--no-merges", "--date=short", "--pretty=format:%ad|%h|%s"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    rows = []
    for line in out.splitlines():
        date, sha, subject = line.split("|", 2)
        rows.append((date, sha, subject))
    return rows


def render(rows: list[tuple[str, str, str]]) -> str:
    by_date: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for date, sha, subject in rows:
        match = CONVENTIONAL.match(subject)
        if match and match["type"] in TYPE_TITLES:
            kind = match["type"]
            scope = f"**{match['scope']}**: " if match["scope"] else ""
            text = f"{scope}{match['subject']}"
        else:
            kind, text = "other", subject
        by_date[date][kind].append(f"{text} (`{sha}`)")

    lines = [
        "# Changelog",
        "",
        "Generated from git history by `.claude/skills/changelog/generate.py`.",
        "Do not edit by hand; merge commits are omitted. Newest date first.",
        "",
    ]
    for date in sorted(by_date, reverse=True):
        lines += [f"## {date}", ""]
        for kind, title in TYPE_TITLES.items():
            if kind not in by_date[date]:
                continue
            lines += [f"### {title}", ""]
            lines += [f"- {item}" for item in by_date[date][kind]]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="CHANGELOG.md")
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    Path(args.output).write_text(render(git_log(args.ref)), encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
