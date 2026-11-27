"""A run's completion checked by Node's own commit-route schema (CHILDREF).

The in-memory journal is Node's twin for rules, not for wire limits: a child completion in a manual test passed it and was
refused by Node (``childResult.resultRef.ref`` over 4,096 characters). ``node_checked`` sends each completion's
transition, as the production client words it (``transition_wire``), through ``commitRunSchema`` before the
journal takes it, and refuses it as Node's route would (400 ``VALIDATION_ERROR``).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.journal_node.client import transition_wire
from harness_core.journal.types import CommitRejected, Complete
from tests.unit.contracts_harness.support import NODE_APP, node_available

if TYPE_CHECKING:
    from harness_core.journal.memory import InMemoryJournal
    from harness_core.journal.types import CommitReceipt, CommitRequest

COMMIT_CLI = NODE_APP / "tests" / "support" / "harness" / "commit-cli.ts"


def require_node() -> None:
    missing = node_available()
    if missing is None:
        return
    if os.environ.get("PXH_REQUIRE_NODE") == "1":
        pytest.fail(f"PXH_REQUIRE_NODE=1 but {missing}")
    pytest.skip(missing)


def node_verdicts(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each worker commit body as Node's route schema judges it: ``{"ok": true}`` or ``{"ok": false, "error"}``."""
    env = {**os.environ, "TS_NODE_PROJECT": str(NODE_APP / "tsconfig.test.json"), "TS_NODE_TRANSPILE_ONLY": "true"}
    proc = subprocess.run(
        ["node", "-r", "ts-node/register", str(COMMIT_CLI)],
        input=json.dumps({"items": bodies}),
        capture_output=True,
        text=True,
        cwd=NODE_APP,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    verdicts = json.loads(proc.stdout)
    assert len(verdicts) == len(bodies)
    return verdicts


def completion_body(request: CommitRequest) -> dict[str, Any]:
    assert request.transition is not None
    return {"commandId": "c-1", "attemptId": "a-1", "fence": 1, "transition": transition_wire(request.transition)}


def node_checked(journal: InMemoryJournal) -> list[dict[str, Any]]:
    """Check every completion ``journal`` is sent with Node's schema; returns the verdicts as they come."""
    commit = journal.commit
    verdicts: list[dict[str, Any]] = []

    async def checked(request: CommitRequest) -> CommitReceipt:
        if isinstance(request.transition, Complete):
            (verdict,) = node_verdicts([completion_body(request)])
            verdicts.append(verdict)
            if not verdict["ok"]:
                raise CommitRejected("VALIDATION_ERROR")
        return await commit(request)

    journal.commit = checked  # type: ignore[method-assign]
    return verdicts
