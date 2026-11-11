"""The operator view: for each connector of an organisation, what keeps its scope stamps from being used and what (if anything) will clear it.

Read-only. It reads the app document of each connector (`connector_view`) and, where the vector store can say, the one condition no document shows:
group rows of pgvector computed after the database's clock (`org_view`). A line is "attention" when something is hidden, stuck or needs a person; a
write still inside its lease and a rewriter that holds the connector are information, not attention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.org_context import for_org
from app.services.permissions.currency import app_is_current
from app.services.permissions.metrics import SERVING_UNREADABLE_ROWS
from app.services.permissions.pending import PendingUnreadable, parse
from app.services.permissions.rewrite_lease import REWRITE_LEASE_KEY, read_lease
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPEN_WRITE_GRACE_SECONDS,
    OPEN_WRITES_KEY,
    OPENED_KEY,
    PENDING_KEY,
    UNSAFE_KEY,
    OpenWritesUnreadable,
    parse_open_writes,
    store_now,
    structure_generation_of,
    unclosed_writes,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS
from app.services.vector_db.scope_sweep import SWEEP_KEY, read_state

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService

#: pgvector group rows computed this far ahead of the database's clock are reported (a row a few seconds ahead clears itself).
GROUP_AHEAD_SECONDS = 30.0
_TRACKED = (GENERATION_KEY, UNSAFE_KEY, PENDING_KEY, OPEN_WRITES_KEY, OPENED_KEY, CLOSED_KEY, SWEEP_KEY, REWRITE_LEASE_KEY, *BACKFILL_KEYS.values())


@dataclass
class ConnectorView:
    connector_id: str
    current: bool
    #: what needs a person or is hidden/stuck, as "code: text"; empty when nothing does
    attention: list[str] = field(default_factory=list[str])
    #: what is happening and will clear itself
    info: list[str] = field(default_factory=list[str])

    def line(self) -> str:
        state = "current" if self.current else "not current"
        return f"{self.connector_id}  {state}" + "".join(f"\n    ! {a}" for a in self.attention) + "".join(f"\n    . {i}" for i in self.info)


@dataclass
class OrgView:
    org_id: str
    connectors: list[ConnectorView]
    #: pgvector group rows computed ahead of the database clock: (rows, furthest in seconds), or None when the store cannot say or none are
    group_rows_ahead: tuple[int, float] | None = None
    #: why a part of the view is missing (the vector store could not be asked)
    notes: list[str] = field(default_factory=list[str])

    @property
    def needs_attention(self) -> bool:
        return bool(self.group_rows_ahead or self.notes or any(c.attention for c in self.connectors))

    def lines(self) -> list[str]:
        out = [c.line() for c in self.connectors]
        if self.group_rows_ahead:
            rows, ahead = self.group_rows_ahead
            out.append(f"! group_rows_ahead: {rows} pgvector group row(s) were computed up to {int(ahead)} s after the database's clock; a rewrite of their "
                       "virtual records is refused (the roots stay pending) until the clock passes them, or they are indexed again")
        out += [f"! {n}" for n in self.notes]
        return out


def status_line(app: Mapping[str, Any]) -> str:
    """One line for `status`: the pending roots and the sweep's last error."""
    try:
        roots = f"{len(parse(app.get(PENDING_KEY)))} pending root(s)"
    except PendingUnreadable:
        roots = "pending roots unreadable"
    state = read_state(app.get(SWEEP_KEY))
    return roots + (f", last error ({state.failures} in a row): {state.error}" if state.failures else ", no error")


def connector_view(connector_id: str, app: Mapping[str, Any], *, clock: Callable[[], float] = time.time, store_time: float | None = None) -> ConnectorView:
    """``clock`` (this host's) reads the sweep's retry time and the rewriter's lease, which are written by it; ``store_time`` reads the open-write leases."""
    host_now = clock()
    now = host_now if store_time is None else store_time
    v = ConnectorView(connector_id, app_is_current(app))
    generation = structure_generation_of(app)
    # ---- pending roots
    unreadable = False
    try:
        roots = parse(app.get(PENDING_KEY))
    except PendingUnreadable:
        unreadable = True
        v.attention.append("pending_unreadable: the pending roots do not parse; the connector stays not current until a full back-fill")
        roots = {}
    if roots:
        gap = f", the oldest registered {generation - min(roots.values())} change(s) ago" if generation is not None else ""
        v.attention.append(f"pending_roots: {len(roots)} root(s) hidden until rewritten{gap}")
    # ---- writes that opened the connector
    in_flight = unclosed_writes(app)
    try:
        leases = parse_open_writes(app.get(OPEN_WRITES_KEY))
    except OpenWritesUnreadable:
        leases = None
        v.attention.append("open_writes_unreadable: the list of leased writes does not parse; close the writes with the back-fill's --close-open-mark")
    if in_flight is None:
        v.attention.append("writes_unreadable: the opened and closed counters do not read; run the back-fill with --close-open-mark once no write is running")
    elif in_flight:
        leased = leases or {}
        alive = [e for e in leased.values() if e.get("k") or e["u"] > now - OPEN_WRITE_GRACE_SECONDS]      # the sweep does not take a `k` lease's deadline as dead
        live = len(alive)
        dead = len(leased) - live
        unleased = in_flight - len(leased) if leases is not None else in_flight
        if dead:
            v.attention.append(f"open_write_expired: {dead} write(s) opened the connector and never closed it; the sweep closes them (run it)")
        if unleased > 0:
            v.attention.append(f"open_write_unleased: {unleased} write(s) have no lease to run out; run the back-fill with --close-open-mark once no write is running")
        if live:
            left = int(min(e["u"] for e in alive) - now)
            v.info.append(f"{live} write(s) in flight, the next lease " + (f"runs out in {left} s" if left > 0 else "has run out and is inside its grace"))
    # ---- the rest of why it is not current
    if v.current is False and not roots and not unreadable and in_flight == 0 and app.get(BACKFILL_KEYS["state"]) is not None:
        v.attention.append("needs_full_pass: nothing is pending and no write is open, but the stamps are not current; run the back-fill")
    if app.get(UNSAFE_KEY) and v.current is False and not roots:
        v.info.append("an unsafe mark is set; a back-fill clears it")
    if (stale := app.get(BACKFILL_KEYS["stale"])):
        v.attention.append(f"stale_points: the last back-fill left {stale} virtual record(s) on the stale marker (served by the legacy filter)")
    # ---- the sweep and the rewriter
    state = read_state(app.get(SWEEP_KEY))
    if state.failures:
        v.attention.append(f"sweep_failing: {state.failures} failure(s) in a row, next try in {max(0, int(state.retry_at - host_now))} s: {state.error}")
    if lease := read_lease(app.get(REWRITE_LEASE_KEY)):
        v.info.append(f"a rewriter holds the connector until {int(lease[1] - host_now)} s from now" if lease[1] > host_now else "a rewriter's lease has run out")
    return v


async def org_view(graph_provider: IGraphDBProvider, org_id: str, vector_db: IVectorDBService | None = None, *,
                   connectors: list[str] | None = None, clock: Callable[[], float] | None = None) -> OrgView:
    """Raises only when the connectors cannot be listed; a vector store that cannot answer is a note."""
    with for_org(org_id, job="scope-view"):
        apps = await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []
        rows = []
        now = await store_now(graph_provider, clock)      # leases are read by the store's clock, as the sweep reads them
        for app in sorted(apps, key=lambda a: str(a.get("_key") or a.get("id"))):
            key = str(app.get("_key") or app.get("id"))
            if (connectors is None or key in connectors) and any(k in app for k in _TRACKED):
                rows.append(connector_view(key, app, clock=clock or time.time, store_time=now))
        view = OrgView(org_id, rows)
        if now is None:
            view.notes.append("store_clock_unknown: the graph store could not say its time; lease deadlines were read by this host's clock and the sweep recovers nothing until it can")
        ask = getattr(vector_db, "group_clock_skew", None)
        if ask is not None:
            try:
                n, ahead = await ask(org_id, min_ahead_seconds=GROUP_AHEAD_SECONDS)
                view.group_rows_ahead = (n, ahead) if n else None
            except Exception as exc:      # the answer is missing, not "none ahead": say so
                view.notes.append(f"group_clock_unknown: the vector store could not say whether group rows are ahead of its clock ({type(exc).__name__})")
        unreadable = getattr(vector_db, "unreadable_group_rows", None)
        if unreadable is not None:
            try:
                n = await unreadable(org_id)
                SERVING_UNREADABLE_ROWS.set("pgvector", org_id, value=n)
                if n:
                    view.notes.append(f"unreadable_group_rows: {n} pgvector group row(s) hold a trusted stamp with a NULL list, which can hide their points in a served search; index them again")
            except Exception as exc:
                view.notes.append(f"unreadable_group_rows_unknown: the vector store could not count them ({type(exc).__name__})")
        return view
