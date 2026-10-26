# ADLC

A local, file-based agentic SDLC tool. It takes work items from a Markdown board (`docs/WORK-ITEMS.md`) and carries each one through test-first implementation, independent review and a merged pull request, escalating to a person only when a defined trigger fires.

The design is in [`PRD.md`](../PRD.md). The build plan is the board in [`WORK-ITEMS.md`](WORK-ITEMS.md), explained item by item in [`WORK-ITEM-WALKTHROUGH.md`](WORK-ITEM-WALKTHROUGH.md).

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows (Git Bash); use .venv/bin/python on macOS/Linux
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m pytest
```
