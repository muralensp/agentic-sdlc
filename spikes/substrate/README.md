# Substrate prototype (ADLC-032)

Throwaway prototype, not part of the `adlc` package. Findings: `docs/research/claude-code-substrate.md`.

- `hooks/guard_edits.py`: `PreToolUse` hook that denies Edit/Write/MultiEdit/NotebookEdit on protected paths (`ADLC_PROTECTED`, semicolon separated; default board, `.github/`, `.adlc/`, `.claude/`).
- `hooks/stop_dod.py`: `Stop` hook that runs `ADLC_DOD` and blocks the stop while it is red, at most `ADLC_MAX_BLOCKS` times per session (state in `ADLC_STATE_DIR`).
- Tests: `tests/test_substrate_hooks.py`.

To try it, make a scratch project with a failing test, then add `.claude/settings.json`:

```json
{"hooks": {
  "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit",
    "hooks": [{"type": "command", "command": "python /path/to/hooks/guard_edits.py"}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "python /path/to/hooks/stop_dod.py"}]}]}}
```

and run `claude -p "<task>" --model haiku --setting-sources project --output-format json --no-session-persistence --allowedTools Read Edit`.
