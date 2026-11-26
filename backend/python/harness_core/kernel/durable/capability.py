"""How a run leaves ``capability_lost`` (PA-7, ADR D25).

A pinned capability that is missing or changed is re-checked a bounded number
of times (a ``yield`` with backoff, so a restarting MCP server or a transient
outage recovers on the same pin), then the run fails ``capability_lost``. It
never waits unbounded (it holds the session's active-run slot) and it is never
re-pinned: a new capability set is a new authority decision, i.e. a new run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.journal.ports import JournalPort
    from harness_core.journal.types import Claim, JournalEvent

CAPABILITY_RECHECK_S: tuple[float, ...] = (30.0, 120.0, 480.0)
CAPABILITY_WAIT = "capability_wait"
CAPABILITY_LOST = "capability_lost"
# A sandbox the broker cannot make now (its capacity, or the broker restarting) is waited for, not counted as a
# crash (QA31F): 465 s in all, then the run fails with a reason the user may retry.
SANDBOX_RECHECK_S: tuple[float, ...] = (15.0, 30.0, 60.0, 120.0, 240.0)
SANDBOX_WAIT = "sandbox_wait"
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
# A store an engine run saves to (the blob store under its checkpoints, snapshots and workspace objects, or Node's
# workspace store) that cannot be reached is waited out the same way (CH-09): 470 s in all, then a typed failure.
STORE_RECHECK_S: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0, 120.0, 240.0)
STORE_WAIT = "store_wait"
STORE_UNAVAILABLE = "store_unavailable"

# A granted tool whose item is not connected for the run's owner now (a sign-in the vendor rejected and that was
# withdrawn, or one never made): still the run's capability, listed and waiting on the connect card (G-12, WP-64).
# Its sign-in is not a capability: moving to or from this mark is no change the run must stop for.
NEEDS_CONNECT = "needs_connect"

# A granted tool whose server answered its listing without it at its pinned digest (dropped, renamed or changed
# upstream, WP-79): the attempt was built without it, and its observation is this mark with the server's label. A
# run that had the tool on an earlier attempt depends on it; waiting cannot bring back what a server that answers
# no longer lists, so it fails at once (``TOOL_WITHDRAWN``) with words that name the server.
WITHDRAWN = "withdrawnBy"
TOOL_WITHDRAWN = "mcp_tool_withdrawn"


def withdrawn_mark(server: str) -> dict[str, str]:
    return {WITHDRAWN: server}


def withdrawn_servers(observed: Mapping[str, object], changed: Sequence[str]) -> list[str]:
    """The servers that withdrew a capability among ``changed``, by their labels."""
    servers: set[str] = set()
    for key in changed:
        mark = observed.get(key)
        if isinstance(mark, dict) and isinstance(label := cast("dict[str, object]", mark).get(WITHDRAWN), str):
            servers.add(label)
    return sorted(servers)


def _is_withdrawn(value: object) -> bool:
    return isinstance(value, dict) and WITHDRAWN in cast("dict[str, object]", value)


def returned(recorded: Mapping[str, object], observed: Mapping[str, object]) -> dict[str, object]:
    """The capabilities the run's first attempt was built without (a ``WITHDRAWN`` mark) that this attempt's build
    found again, each with the mark it was recorded with (WP79-FU-5). The run never held them, so they stay withheld
    for the whole run: the attempt is asked to withhold them again (``CapabilityCheck.withhold``) before its first
    model call, and the next run offers them. A new capability set is a new authority decision, i.e. a new run."""
    return {
        key: mark
        for key, mark in recorded.items()
        if _is_withdrawn(mark) and key in observed and not _is_withdrawn(observed[key])
    }


def capability_changes(recorded: Mapping[str, object], observed: Mapping[str, object]) -> list[str]:
    """The capabilities that changed between attempts, a sign-in that came or went aside (``NEEDS_CONNECT``)."""
    return sorted(
        key
        for key in {*recorded, *observed}
        if recorded.get(key) != observed.get(key)
        and NEEDS_CONNECT not in (recorded.get(key), observed.get(key))
    )


async def capability_waits(journal: JournalPort, claim: Claim, reason: str = CAPABILITY_WAIT) -> int:
    """Re-checks this run already spent (its ``run.yielded`` with ``reason``)."""
    return count_capability_waits(await journal.read(claim.stream_id), claim, reason)


def count_capability_waits(events: Sequence[JournalEvent], claim: Claim, reason: str = CAPABILITY_WAIT) -> int:
    return sum(
        1
        for event in events
        if event.run_id == claim.run_id
        and event.type == "run.yielded"
        and event.data.get("reason") == reason
    )


def recheck_delay(waited: int, schedule: tuple[float, ...] = CAPABILITY_RECHECK_S) -> float | None:
    """Seconds until the next re-check, or ``None`` once the bound is spent."""
    return schedule[waited] if waited < len(schedule) else None


__all__ = [
    "CAPABILITY_LOST",
    "CAPABILITY_RECHECK_S",
    "CAPABILITY_WAIT",
    "NEEDS_CONNECT",
    "SANDBOX_RECHECK_S",
    "SANDBOX_UNAVAILABLE",
    "SANDBOX_WAIT",
    "STORE_RECHECK_S",
    "STORE_UNAVAILABLE",
    "STORE_WAIT",
    "TOOL_WITHDRAWN",
    "WITHDRAWN",
    "capability_changes",
    "capability_waits",
    "count_capability_waits",
    "recheck_delay",
    "returned",
    "withdrawn_mark",
    "withdrawn_servers",
]
