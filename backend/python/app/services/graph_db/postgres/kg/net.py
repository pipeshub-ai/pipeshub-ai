"""The net under the reader: what catches a kg that holds something the graph does not, once a connector is enforced.

The reader trusts every row kg holds for a served connector. Verifying the connector when it is promoted proves the rows were right then; two checks keep watching afterwards:

- a share of the checks the reader answers (``PG_KG_SHADOW_SAMPLE``, 1 % unless set) is repeated by the walk, off the caller's path, in one snapshot (:func:`sample`, called by ``read``);
- the worker verifies each enforced connector now and then (``PG_KG_VERIFY_EVERY_S``, an hour unless set, 0 = never), a few connectors per pass (:func:`verify_due`).

Any difference takes the connector back to the walk at once (``backfill.demote``: mode legacy, no cursor), logs an error and is counted: kg admitting what the walk denies is a leak, and kg
denying what the walk admits is a gap the reader must not have either. The connector is taken to enforce again only by the bootstrap (copy, verify, promote), and not before ``HOLD_S``
has passed, so a difference the copy cannot remove does not flap. A sample or a verify that fails changes nothing: it never promotes, and never reads more than before.

The query service reads kg with a role that cannot write it: its demotion is a notice (``pg_notify``, which any role may send; it can only ask for the safe direction) that the worker turns
into the demotion.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.org_context import bind, spawn_detached
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.kg import backfill, bootstrap, drainer, locks
from app.services.graph_db.postgres.kg.verify import (
    Cursor,
    verify_chunk,
    verify_principals,
)
from app.services.graph_db.postgres.kg.writers import KgWriter
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
SAMPLE_ENV, EVERY_ENV = "PG_KG_SHADOW_SAMPLE", "PG_KG_VERIFY_EVERY_S"
CHANNEL = "pipeshub_kg_demote"
SAMPLE_IDS = 500                 # nodes kg decided that a sample repeats (a call decides up to tens of thousands; each node is decided on its own)
# Samples in flight in one process. Each holds one connection of the service's pool for up to the walk's 10 s limit, and the pool is PG_POOL_MAX (10 by default): at most a quarter of it,
# so the requests that matter keep their connections even if every sample is slow (a test holds this to DbConfig's default).
MAX_SAMPLES = 2
VERIFY_PER_PASS = 2              # connectors one verify pass works on; the rest wait for the next pass
VERIFY_TICK_S = 5.0              # how often the verify task looks for a connector whose turn has come
SKIP_RETRY_S = 30.0              # a verify that found a change waiting in the log starts again this much later, from the beginning
VERIFY_BUDGET_S = 20.0           # seconds one connector's chunks run per pass; the next pass goes on from the cursor
VERIFY_TIMEOUT = "30s"           # one chunk of a verify (a page of a connector), in a snapshot of its own
OVERDUE_INTERVALS = 24           # a full verify not completed within this many intervals is logged as an error and raised on the gauge
MARKER = "3:@demote"             # the one key of a demotion request in the change log, a record that is not there. The drain never takes a row that holds it (drainer.py), so it stays until the worker has acted
# One worker serves the org (OSS runs one process), so the hold and the distrusted set are per process: a second process would hold its own.
HOLD_S = 300.0                   # after a demotion by the net, the bootstrap leaves the connector alone this long

NET = METRICS_BACKEND.counter("pipeshub_kg_net_total", "Checks of an enforced connector against the walk, by where they ran and what they found (over: kg admitted what the walk denies; under: kg denied what the walk admits; same; skipped; busy: a sample found the process at its limit; error)", ["source", "result"])
LAST_VERIFIED = METRICS_BACKEND.gauge("pipeshub_kg_net_last_verified_seconds", "Unix time a connector's last complete verify against the graph finished (alert when it is older than a few verify intervals)", ["org", "connector"])
OVERDUE = METRICS_BACKEND.gauge("pipeshub_kg_net_verify_overdue", "Enforced connectors whose full verify has not completed within 24 verify intervals", ["org"])
DEMOTED = METRICS_BACKEND.counter("pipeshub_kg_net_demoted_total", "Connectors taken back to the walk because kg differs from it", ["source"])

ENFORCED = """/* pipeshub:kg.net_enforced */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
    WHERE s.org_id = $1 AND k.coll = 1 AND s.mode = 'enforce' ORDER BY k.id"""

_SAMPLES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Task]] = weakref.WeakKeyDictionary()     # per loop: a loop that died leaves no sample counted as in flight
_RETRY: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Task] = weakref.WeakKeyDictionary()
# Connectors this process has found different from the walk: (org, connector) -> when the copy was last verified (None: not known). ``read`` never answers a node of one from kg, whatever
# happens to the demotion that follows. An entry goes when the connector is seen no longer enforced or verified again (a re-promotion), or at restart.
DISTRUSTED: dict[tuple[str, str], str | None] = {}
_ASKING: dict[tuple[str, str], KernelDB] = {}      # distrusted connectors whose demotion this process has asked the worker for
_ASKED: dict[tuple[str, str], float] = {}          # when it last did, to ask once per hold window


def _number(name: str, default: float, low: float, high: float) -> float:
    """A number from the environment in ``[low, high]``; unset or empty is the default, anything else that is not such a number raises (``check_env``)."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}={raw!r} is not understood: set it to a number from {low:g} to {high:g}, or leave it unset")
    return value


def check_env() -> None:
    sample_rate(), verify_every()


def sample_rate() -> float:
    """The share of the reader's answers that the walk repeats: 0.01 unless set (0 = none, 1 = all)."""
    return _number(SAMPLE_ENV, 0.01, 0.0, 1.0)


def verify_every() -> float:
    """Seconds between two verifies of a connector: an hour unless set (0 = never)."""
    return _number(EVERY_ENV, 3600.0, 0.0, 30 * 86400.0)


# ---- distrust: what a difference does in the process that found it, at once ---------------------------------------------------------------------

STATE = """/* pipeshub:kg.net_state */ SELECT s.mode, s.verified_at::text AS at FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
    WHERE s.org_id = $1 AND k.coll = 1 AND k.id = $2"""


def distrust(org: str, app: str) -> None:
    """From now on this process answers no node of the connector from kg. Synchronous and first: nothing that follows (a demotion that fails, a notice nobody hears) can undo it."""
    DISTRUSTED.setdefault((org, app), None)


async def distrusted(tx: Tx, org: str, app: str) -> bool:
    """Whether this process distrusts the connector. A connector seen no longer enforced, or verified again since (a re-promotion), is trusted again, as the worker's mode change says."""
    if (org, app) not in DISTRUSTED:
        return False
    row = await tx.fetchrow(STATE, org, app)
    at = DISTRUSTED[(org, app)]
    if row is None or row["mode"] != "enforce" or (at is not None and row["at"] != at):
        DISTRUSTED.pop((org, app), None)
        _ASKING.pop((org, app), None)
        return False
    return True


async def _remember(db: KernelDB, org: str, app: str) -> None:
    """Note when the distrusted copy was last verified, so that a re-promotion (which verifies again) is recognised. Best effort: unknown means trusted again only when seen not enforced."""
    async def go(tx: Tx) -> str | None:
        row = await tx.fetchrow(STATE, org, app)
        return row["at"] if row and row["mode"] == "enforce" else None
    try:
        with bind(drainer._ctx(org)):
            at = await db.run(go)
    except Exception:
        return
    if (org, app) in DISTRUSTED and at is not None:
        DISTRUSTED[(org, app)] = at


async def demote(db: KernelDB, org: str, apps: Iterable[str], *, source: str, why: str) -> None:
    """Take these connectors back to the walk. This process distrusts them first. A process whose role may not write kg (the query service) asks the worker instead, by a notice and a
    request row in the change log, and asks again each hold window until it sees the connector's mode change."""
    for app in sorted(set(apps)):
        distrust(org, app)
        logger.error("kg connector %s of org %s goes back to the walk (%s): %s", app, org, source, why)
        DEMOTED.inc(source)
        bootstrap.hold(org, app, HOLD_S)
        await _remember(db, org, app)                       # before anything that can make the worker act: a mode already legacy would leave the time unknown
        try:
            await backfill.demote(db, org, app)
        except Exception:
            logger.info("kg could not demote %s here (this role may not write kg); asking the worker", app)
            _ASKING[(org, app)] = db
            await _ask(db, org, app)
            _keep_asking()


async def _ask(db: KernelDB, org: str, app: str, *, force: bool = False) -> None:
    """A notice (fast, lost if nobody listens) and a request row in the change log (durable: the worker finds it on its next pass). Once per connector per hold window unless ``force``."""
    now = time.monotonic()
    if not force and _ASKED.get((org, app), 0.0) > now:
        return
    _ASKED[(org, app)] = now + HOLD_S

    async def go(tx: Tx) -> None:
        await tx.execute("/* pipeshub:kg.demote_ask */ SELECT pg_notify($1, $2)", CHANNEL, json.dumps([org, app]))
        await tx.execute("/* pipeshub:kg.demote_mark */ INSERT INTO kg.graph_changes (org_id, app_id, ends) VALUES ($1, $2, ARRAY[$3]::text[])", org, app, MARKER)
    try:
        with bind(drainer._ctx(org)):
            await db.run(go, write=True)
    except Exception:
        _ASKED.pop((org, app), None)                        # nothing was asked: the next detection asks at once
        logger.warning("kg could not ask for the demotion of %s", app, exc_info=True)


def _keep_asking() -> None:
    """One task per event loop that asks again every hold window for each distrusted connector still enforced, until the mode change is seen."""
    loop = asyncio.get_running_loop()
    if (task := _RETRY.get(loop)) is not None and not task.done():
        return

    async def again() -> None:
        while _ASKING:
            await asyncio.sleep(HOLD_S)
            for (org, app), db in list(_ASKING.items()):
                try:
                    with bind(drainer._ctx(org)):
                        row = await db.run(lambda tx, o=org, a=app: tx.fetchrow(STATE, o, a))
                except Exception:
                    row = {"mode": "enforce"}               # cannot tell: ask again
                if (org, app) not in DISTRUSTED or row is None or row["mode"] != "enforce":
                    _ASKING.pop((org, app), None)           # the worker acted (or the connector is gone): no more asking, and the next read lifts the distrust
                    continue
                await _ask(db, org, app, force=True)
    _RETRY[loop] = spawn_detached(again(), name="kg-demote-ask")


def parse_ask(payload: str) -> tuple[str, str] | None:
    """``(org, connector)`` of a demotion notice, or None for a payload that is not one (any role may send anything)."""
    try:
        org, app = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return (org, app) if isinstance(org, str) and isinstance(app, str) else None


async def demote_requested(db: KernelDB, orgs: Callable[[], Awaitable[list[str]]], payload: str) -> None:
    """The worker's side of the notice. Only an organization the worker serves is acted on."""
    try:
        ask = parse_ask(payload)
        if ask is None:
            logger.warning("kg ignored a demotion notice that is not [org, connector]: %r", payload[:200])
        elif ask[0] in await orgs():
            logger.error("kg connector %s of org %s goes back to the walk (asked by another process)", ask[1], ask[0])
            bootstrap.hold(ask[0], ask[1], HOLD_S)
            await backfill.demote(db, ask[0], ask[1])
    except Exception:
        logger.exception("kg demotion asked for (%r) failed", payload[:200])


async def demote_pending(db: KernelDB, org: str) -> int:
    """The worker's side of the request rows: demote each connector one names and delete the row. Works with no listener, so a notice nobody heard is not a demotion lost."""
    async def find(tx: Tx) -> list[Any]:
        return list(await tx.fetch("/* pipeshub:kg.demote_find */ SELECT id, app_id FROM kg.graph_changes WHERE org_id = $1 AND ends @> ARRAY[$2]::text[] ORDER BY id", org, MARKER))
    with bind(drainer._ctx(org)):
        rows = await db.run(find)
    for app in sorted({r["app_id"] for r in rows if r["app_id"]}):
        logger.error("kg connector %s of org %s goes back to the walk (asked by another process)", app, org)
        bootstrap.hold(org, app, HOLD_S)
        await backfill.demote(db, org, app)

    async def drop(tx: Tx) -> None:
        await KgWriter(tx).changes_delete([r["id"] for r in rows])
    if rows:
        with bind(drainer._ctx(org)):
            await db.run(drop, write=True)
    return len({r["app_id"] for r in rows})


async def shutdown() -> None:
    """Cancel and await what this loop has in flight: the samples and the asking task."""
    loop = asyncio.get_running_loop()
    tasks = [*list(_SAMPLES.get(loop, ())), *([t] if (t := _RETRY.get(loop)) is not None else [])]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _SAMPLES.pop(loop, None)
    _RETRY.pop(loop, None)


# ---- the sample ------------------------------------------------------------------------------------------------------------------------------

async def compare(tx: Tx, org: str, ids: list[str], access: dict[str, Any]) -> tuple[set[str], str]:
    """In the caller's snapshot: the connectors whose answers differ between kg and the walk for the ids (nodes kg decided), and what the difference is (``over`` if kg admitted something
    the walk denied, else ``under``)."""
    from app.services.graph_db.postgres.kg import read
    await tx.fetchval(read.PROFILE)
    d = await read.decide(tx, org, ids, access)
    decided = d.admit | d.deny
    if not decided:
        return set(), "undecided"
    await tx.fetchval("/* pipeshub:kg.sample_limits */ SELECT set_config('statement_timeout', '10s', true)")      # the walk is slower than the reader's own limit
    walked = {r["id"] for r in await access_sql.accessible_rows(tx, org, sorted(decided), [], access)}
    over, under = d.admit - walked, d.deny & walked
    return {d.apps[i] for i in over | under}, ("over" if over else "under" if under else "same")


async def _sample(run: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]], db: KernelDB, org: str, ids: list[str], access: dict[str, Any]) -> None:
    try:
        apps, found = await run(lambda tx: compare(tx, org, ids, access))
    except Exception:
        NET.inc("sample", "error")
        logger.warning("kg sample against the walk failed; nothing changes", exc_info=True)
        return
    NET.inc("sample", found)
    if apps:
        await demote(db, org, apps, source="sample", why=f"kg {'admitted what the walk denies' if found == 'over' else 'denied what the walk admits'}")


def maybe_sample(run: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]], db: KernelDB, org: str, decided: Iterable[str], access: dict[str, Any]) -> None:
    """Repeat a share of the reader's answers by the walk, off the caller's path (a task with the caller's context, so its org). ``decided``: the nodes kg answered itself."""
    rate = sample_rate()
    if rate <= 0 or random.random() >= rate:
        return
    loop = asyncio.get_running_loop()
    live = _SAMPLES.setdefault(loop, set())
    if len(live) >= MAX_SAMPLES:
        NET.inc("sample", "busy")
        return
    nodes = list(decided)
    ids = random.sample(nodes, min(len(nodes), SAMPLE_IDS))

    async def in_org() -> None:
        with bind(drainer._ctx(org)):                       # a detached task has no org of its own
            await _sample(run, db, org, ids, access)
    task = spawn_detached(in_org(), name="kg-sample")
    live.add(task)
    task.add_done_callback(live.discard)


async def settle() -> None:
    """Wait for the samples this loop has in flight (tests)."""
    while live := _SAMPLES.get(asyncio.get_running_loop()):
        await asyncio.gather(*list(live), return_exceptions=True)


# ---- the periodic verify --------------------------------------------------------------------------------------------------------------------

ENFORCED = """/* pipeshub:kg.net_enforced */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
    WHERE s.org_id = $1 AND k.coll = 1 AND s.mode = 'enforce' ORDER BY k.id"""


# The planner profile of the reader (no bitmap or sequential scans: with no statistics, or a connector that holds most of the org's rows, one can read the whole org to sort a page) and the limit of one page.
CHUNK_PROFILE = "/* pipeshub:kg.net_verify_limit */ SELECT set_config('enable_bitmapscan', 'off', true), set_config('enable_seqscan', 'off', true), set_config('statement_timeout', $1, true)"


def _jitter() -> float:
    return random.random()


@dataclass
class VerifyState:
    """One connector's place in the verify: when it is next due, and a verify in progress (its cursor, and when it began)."""
    due: float
    cursor: Cursor | None = None
    since: float | None = None
    flagged: bool = False
    mark: int = 0                  # the connector's write count when the scan began (read under its lock, see _begin)


async def _chunk(db: KernelDB, org: str, app: str, cur: Cursor) -> str:
    """One page of the verify in a snapshot of its own: ``skipped`` when the connector is not served in it (a change waits in the log, so kg is behind the graph by design), else ``done`` or
    ``more``."""
    async def go(tx: Tx) -> str:
        await tx.fetchval(CHUNK_PROFILE, VERIFY_TIMEOUT)
        if not await drainer.serves(tx, org, app):
            return "skipped"
        return "done" if await verify_chunk(tx, org, app, cur) else "more"
    with bind(drainer._ctx(org)):
        return await db.run(go, isolation="repeatable_read", custom_plan=True)


async def _begin(db: KernelDB, org: str, app: str) -> int:
    """The write count of the connector, read under its lock: a unit that has counted and not yet committed holds the lock, so this waits for it, and what the first page reads includes it."""
    async def go(tx: Tx) -> int:
        await locks.lock_apps(tx, org, {app}, org_wide=False)
        return locks.written(org, app)
    with bind(drainer._ctx(org)):
        return await db.run(go, write=True)


async def _principals(db: KernelDB, org: str) -> bool:
    async def go(tx: Tx) -> bool:
        await tx.fetchval(CHUNK_PROFILE, VERIFY_TIMEOUT)
        return (await verify_principals(tx, org)).ok
    with bind(drainer._ctx(org)):
        return await db.run(go, isolation="repeatable_read")


async def verify_due(db: KernelDB, org: str, states: dict[tuple[str, str], VerifyState], stop: asyncio.Event | None = None) -> int:
    """Work on the enforced connectors of ``org`` whose turn has come (a verify in progress, or ``due`` passed), at most ``VERIFY_PER_PASS`` of them, each for at most ``VERIFY_BUDGET_S``
    seconds: a page at a time, so a connector too large for one unit is verified over several passes from its cursor. A difference demotes. Returns the verifies completed. Never raises:
    a page that fails drops that verify (it starts again next period) and changes nothing. A first due time is spread over the interval, so a start-up does not verify every connector at once."""
    every = verify_every()
    if every <= 0:
        return 0

    async def enforced(tx: Tx) -> list[str]:
        return [r["id"] for r in await tx.fetch(ENFORCED, org)]
    try:
        with bind(drainer._ctx(org)):
            apps = await db.run(enforced, isolation="repeatable_read")
    except Exception:
        NET.inc("verify", "error")
        logger.warning("kg verify could not list the enforced connectors of an org", exc_info=True)
        return 0
    now = time.monotonic()
    for key in [k for k in states if k[0] == org and k[1] not in apps]:
        del states[key]                                    # no longer enforced (demoted, perhaps re-promoted later): a scan in progress was of another copy
    for app in apps:
        states.setdefault((org, app), VerifyState(due=now + _jitter() * every))
    overdue = 0
    for app in apps:
        st = states[(org, app)]
        if st.since is not None and now - st.since > OVERDUE_INTERVALS * every:
            overdue += 1
            if not st.flagged:
                st.flagged = True
                logger.error("kg connector %s of org %s has not completed a verify in %d intervals", app, org, OVERDUE_INTERVALS)
    OVERDUE.set(org, value=overdue)
    turn = sorted((a for a in apps if states[(org, a)].cursor is not None or states[(org, a)].due <= now), key=lambda a: states[(org, a)].due)[:VERIFY_PER_PASS]
    completed = 0
    for app in turn:
        st = states[(org, app)]
        deadline = time.monotonic() + VERIFY_BUDGET_S
        while True:
            if st.cursor is not None and locks.written(org, app) != st.mark:
                NET.inc("verify", "restarted")                  # rows of the connector were rewritten since the scan began: a key it had passed may be one of them
                st.cursor = None
            if st.cursor is None:
                st.since = st.since or time.monotonic()
                try:
                    mark = await _begin(db, org, app)
                except Exception:                                # the lock is held longer than this unit may wait (a projection unit holds it up to its statement timeout): no scan state survives
                    NET.inc("verify", "busy")
                    st.cursor, st.due = None, time.monotonic() + SKIP_RETRY_S
                    break
                st.cursor, st.mark = Cursor(), mark
            try:
                found = await _chunk(db, org, app, st.cursor)
            except Exception:
                NET.inc("verify", "error")
                logger.warning("kg verify of connector %s failed; it is left as it is and starts again next period", app, exc_info=True)
                st.cursor, st.due = None, time.monotonic() + every
                break
            if found == "skipped":
                NET.inc("verify", "skipped")
                st.cursor, st.due = None, time.monotonic() + SKIP_RETRY_S       # a change that was waiting may have changed what this scan already passed: start again, later
                break
            if found == "more" and st.cursor.res.ok:
                if (stop is not None and stop.is_set()) or time.monotonic() >= deadline:
                    break                                      # a page was done; the next pass goes on from the cursor
                continue
            ok = st.cursor.res.ok
            try:
                ok = ok and found == "done" and await _principals(db, org)
                if found == "done" and locks.written(org, app) != st.mark:      # the last look, after the last step: a rewrite during the final page or the principals is not a complete verify
                    NET.inc("verify", "restarted")
                    st.cursor = None
                    break
            except Exception:
                NET.inc("verify", "error")
                logger.warning("kg verify of the principals failed; connector %s is left as it is", app, exc_info=True)
                st.cursor, st.due = None, time.monotonic() + every
                break
            NET.inc("verify", "same" if ok else "differs")
            if not ok:                                         # the first page that shows a difference: the connector goes back to the walk now, not when the scan ends
                logger.error("kg verify of connector %s of org %s: %s", app, org, st.cursor.res.summary())
                await demote(db, org, [app], source="verify", why="the copy differs from the graph")
            else:
                LAST_VERIFIED.set(org, app, value=time.time())
            st.cursor, st.since, st.flagged, st.due = None, None, False, time.monotonic() + every
            completed += 1
            break
    return completed
