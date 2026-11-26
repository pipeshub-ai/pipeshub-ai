"""PERF-C in the fault catalog: Node dies between a commit and the link of its audit entry.

The entry is committed with its transaction and linked onto the org's chain right after (PERFB-F1's fix). A process
killed in between leaves it committed and unlinked; the restarted Node links it with nothing for an operator to do.
"""

from __future__ import annotations

import json
import urllib.error
import uuid
from typing import Any

import pytest

import stack

pytestmark = pytest.mark.fault

# The link watch looks every 30 s (AUDIT_LINK_WATCH_MS); one interval and a margin for the restart.
LINKED_WITHIN_S = 75


def _audit(run_id: str) -> dict[str, Any]:
    return stack.http("GET", f"/__fault/audit/{stack.ORG}/{run_id}")


def test_perfc_44_an_audit_entry_committed_before_a_crash_is_linked_after_the_restart(fault_stack: stack.FaultStack) -> None:
    run_id = f"crash-{uuid.uuid4()}"
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        stack.http("POST", f"/__fault/audit/{stack.ORG}/{run_id}/crash-after-commit", {})
    assert fault_stack.node.proc is not None
    fault_stack.node.proc.wait(30)

    fault_stack.node.start()
    left = _audit(run_id)
    # Committed and not linked: the crash fell between the two (a read through the store would have linked it).
    assert left["records"] == 0, left
    assert [row["appends"] for row in left["unlinked"]] == [1], left

    stack.wait_for(lambda: _audit(run_id)["records"] == 1, LINKED_WITHIN_S, "the entry on the chain after the restart")
    after = _audit(run_id)
    assert after["unlinked"] == [], after
    with (fault_stack.out / "results.jsonl").open("a") as results:
        results.write(json.dumps({"scenario": "audit_link_after_crash", "broker": fault_stack.broker, "runId": run_id,
                                  "problems": []}) + "\n")
