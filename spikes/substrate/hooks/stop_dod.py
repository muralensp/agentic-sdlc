"""Stop hook: do not let the agent stop while the Definition of Done is red (ADLC-032 prototype).

Runs the DoD command (ADLC_DOD, default `pytest -q`) in the session's directory. Green: allow the
stop. Red: block it and feed the output back, at most ADLC_MAX_BLOCKS times per session, then hand
control back so a stuck agent cannot loop forever (the driver's own DoD run then decides).
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

TAIL = 2000


def decide(payload: dict, command: list[str], state_dir: Path, max_blocks: int) -> dict | None:
    counter = state_dir / f"{payload.get('session_id', 'unknown')}.count"
    try:
        done = subprocess.run(
            command, cwd=payload.get("cwd") or None, capture_output=True, text=True, timeout=600
        )
        code, output = done.returncode, (done.stdout + done.stderr)[-TAIL:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, output = None, f"could not run {command[0]!r}: {exc}"
    if code == 0:
        counter.unlink(missing_ok=True)
        return None
    blocked = int(counter.read_text()) if counter.exists() else 0
    if blocked >= max_blocks:
        return None
    state_dir.mkdir(parents=True, exist_ok=True)
    counter.write_text(str(blocked + 1))
    what = "could not run" if code is None else f"failed (exit {code})"
    return {
        "decision": "block",
        "reason": f"The Definition of Done {what}. Fix it before stopping.\n{output}",
    }


def main() -> int:
    command = shlex.split(os.environ.get("ADLC_DOD") or f'"{sys.executable}" -m pytest -q')
    state_dir = Path(os.environ.get("ADLC_STATE_DIR") or Path(tempfile.gettempdir()) / "adlc-stop")
    max_blocks = int(os.environ.get("ADLC_MAX_BLOCKS", "3"))
    decision = decide(json.load(sys.stdin), command, state_dir, max_blocks)
    if decision:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
