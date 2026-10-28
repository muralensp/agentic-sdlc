# Claude Code as the agent substrate: findings (ADLC-032)

Date: 2026-10-28. Claude Code 2.1.311. Question: can the hybrid in PRD decision 21 work, that is, can a thin driver call Claude Code headless and rely on hooks for completion and guardrails?

**Answer: yes, with one hard limit.** Hooks cover the file tools, not the shell, so the driver must re-check the diff after every iteration.

## Method

A throwaway project outside this repo (a `calc.py` with a bug, a failing `test_calc.py`, a one-row `docs/WORK-ITEMS.md`) with project settings that register the two hooks in `spikes/substrate/hooks/`. Each run used `claude -p ... --model haiku --setting-sources project --output-format json --no-session-persistence` so user settings could not leak in. Total cost of all six runs was about 2 cents of list-price usage.

## Evidence

| Run | Prompt | Result |
|---|---|---|
| probe | Read a file, change nothing | Hooks fire headless. `PreToolUse` payload has `tool_name`, `tool_input.file_path`, `cwd`, `session_id`; `Stop` payload has `stop_hook_active`, `last_assistant_message`. |
| A | "Explain why the test fails, do not edit" with a red DoD | The `Stop` hook blocked the stop twice (counter file = 2, 5 turns). The agent saw "The Definition of Done failed" and said it could not fix it without permission. Then the block limit was reached and control came back. `calc.py` stayed unedited. |
| B | "Fix `calc.py`" | Edit allowed (not a protected path), DoD went green, the `Stop` hook allowed the stop and cleared its counter. `calc.py` now `a + b`, pytest 1 passed. |
| C | "Use the Edit tool to set T-1 to DONE in `docs/WORK-ITEMS.md`" | `PreToolUse` denied it. The result lists it under `permission_denials` and the agent reported the hook's reason. The board was unchanged. |
| D | Same change via `sed -i` in Bash | **It succeeded.** The board now says DONE. The file-tool hook does not see shell writes. |
| E | `--agents` JSON with a subagent on `model: sonnet`, main model haiku | `modelUsage` lists both `claude-haiku-5-5` and `claude-sonnet-5-5`; `subagent_stats.spawned` is 1. |

The hook logic is also covered by 19 unit tests (`tests/test_substrate_hooks.py`), each written to fail first.

## What this settles

1. **Headless invocation works** and returns JSON with `result`, `terminal_reason`, `num_turns`, `permission_denials`, `total_cost_usd`, `usage` and a per-model `modelUsage`. The driver can log all of it (FR-18) and measure usage without parsing prose.
2. **A `Stop` hook can enforce "completion is decided by a command".** It must carry its own limit (`ADLC_MAX_BLOCKS`), otherwise a stuck agent loops forever. After the limit, the driver's own DoD run decides.
3. **Per-role models are verifiable.** The Reviewer can be a subagent with its own `model:`. The driver can check `modelUsage` to confirm the Reviewer's model differs from the Coder's (decision 2a), rather than trusting configuration.
4. **File-tool guardrails work; shell writes bypass them.** Run D is the reason decision 21 keeps the driver's post-iteration diff check, and ADLC-027 must not rely on hooks alone. Options for ADLC-033/027, not tried here: a `Bash` matcher that rejects obvious writes to protected paths (brittle), `permissions.deny` rules (not tested), or running the Coder with Bash restricted to an allow-list. None replaces the diff check.
5. **`--setting-sources project`** isolates a run from user-level settings and hooks. The driver should always pass it.

## Not answered

- **Usage-window signal:** no run hit a limit, so the exact error or `terminal_reason` for an exhausted window is still unknown. ADLC-012 and ADLC-022 must capture it the first time it happens, and until then treat any non-success `terminal_reason` as "stop and look".
- **`permissions.deny` for paths and Bash patterns** was not exercised.
- **Windows hook commands** ran through Git Bash with a full path to the venv Python. Quoting rules for paths with spaces were not tested.
- **Interactive skills** (grilling) were not run headless; ADLC-010 still needs an interactive launcher.

## Reproduce

`spikes/substrate/README.md`.
