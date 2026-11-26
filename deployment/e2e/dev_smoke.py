"""G-50 (C20.51): the check that a laptop's dev-harness stack serves a turn. No live model call: the turn runs on
the stack's scripted model (a ``say`` directive, harness_core/testing/engines/script.py) through the durable worker
and the model gateway, read as the web app reads it.

    python3 dev_smoke.py <run dir>      e.g. ~/.cache/pxh-e2e/<run>

Prints one line and exits non-zero when the turn did not end with the scripted answer.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    here = Path(__file__).resolve().parent
    sys.path[:0] = [str(here), str(here.parents[1] / "integration-tests/harness/e2e")]
    from lane import Stack
    from smoke_human import TERMINAL, answer, turn

    stack = Stack.attach(run_dir.name, "dev-harness")
    marker = f"dev-{secrets.token_hex(3)}"
    frames, session, run = turn(stack, stack.seed["agentKey"], f"say {marker}")
    last = frames[-1].type if frames else "none"
    passed = bool(run) and last in TERMINAL and marker in answer(frames)
    print(f"{'ok' if passed else 'FAIL'}: scripted turn (conversation {session}, run {run}, ended {last})")  # noqa: T201 - the check's one line of output
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
