---
name: changelog
description: Regenerate CHANGELOG.md from git commits, grouped by date and Conventional Commit type. Use when the user asks to create, update or refresh the changelog.
---

# Changelog

CHANGELOG.md is derived from git history, so regenerate it rather than editing it.

1. Run from the repo root: `.venv/Scripts/python .claude/skills/changelog/generate.py`
   (`.venv/bin/python` outside Windows). Options: `--output <file>`, `--ref <branch-or-tag>`.
2. Read the exit code directly (no piping) and skim `CHANGELOG.md` for sanity.
3. Show the user `git diff --stat CHANGELOG.md`. Commit only if they ask.

Merge commits are skipped. Commits that are not Conventional Commits land under "Other".
