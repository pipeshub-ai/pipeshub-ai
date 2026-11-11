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
from typing import TYPE_CHECKING, Any

from app.core.org_context import bind
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.kg import backfill, bootstrap, drainer
from app.services.graph_db.postgres.kg.verify import verify_app, verify_principals
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
SAMPLE_ENV, EVERY_ENV = "PG_KG_SHADOW_SAMPLE", "PG_KG_VERIFY_EVERY_S"
CHANNEL = "pipeshub_kg_demote"
SAMPLE_IDS = 500                 # asked nodes a sample repeats (a call asks for up to tens of thousands; each node is decided on its own)
MAX_SAMPLES = 4                  # samples in flight in one process: a busy process skips the sample rather than queue the walk
VERIFY_PER_PASS = 2              # connectors one worker pass verifies; the rest wait for the next pass
VERIFY_TIMEOUT = "120s"          # a verify reads every record of the connector in one snapshot; a connector too large for it is cut here (counted as an error) and tried again next period
HOLD_S = 300.0                   # after a demotion by the net, the bootstrap leaves the connector alone this long

NET = METRICS_BACKEND.counter("pipeshub_kg_net_total", "Checks of an enforced connector against the walk, by where they ran and what they found (over: kg admitted what the walk denies; under: kg denied what the walk admits; same; skipped; busy: a sample found the process at its limit; error)", ["source", "result"])
DEMOTED = METRICS_BACKEND.counter("pipeshub_kg_net_demoted_total", "Connectors taken back to the walk because kg differs from it", ["source"])

ENFORCED = """/* pipeshub:kg.net_enforced */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
    WHERE s.org_id = $1 AND k.coll = 1 AND s.mode = 'enforce' ORDER BY k.id"""

_SAMPLES: set[asyncio.Task] = set()
_WARNED: set[tuple[str, str]] = set()


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


async def demote(db: KernelDB, org: str, apps: Iterable[str], *, source: str, why: str) -> None:
    """Take these connectors back to the walk. A process whose role may not write kg (the query service) asks the worker instead."""
    for app in sorted(set(apps)):
        logger.error("kg connector %s of org %s goes back to the walk (%s): %s", app, org, source, why)
        DEMOTED.inc(source)
        bootstrap.hold(org, app, HOLD_S)
        try:
            await backfill.demote(db, org, app)
        except Exception:
            logger.warning("kg could not demote %s here; asking the worker", app, exc_info=True)
            await _ask(db, org, app)


async def _ask(db: KernelDB, org: str, app: str) -> None:
    try:
        with bind(drainer._ctx(org)):
            await db.run(lambda tx: tx.execute("/* pipeshub:kg.demote_ask */ SELECT pg_notify($1, $2)", CHANNEL, json.dumps([org, app])))
    except Exception:
        logger.exception("kg could not ask for the demotion of %s", app)


def parse_ask(payload: str) -> tuple[str, str] | None:
    """``(org, connector)`` of a demotion notice, or None for a payload that is not one (any role may send anything)."""
    try:
        org, app = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return (org, app) if isinstance(org, str) and isinstance(app, str) else None


async def demote_requested(db: KernelDB, orgs: Callable[[], Awaitable[list[str]]], payload: str) -> None:
    """The worker's side of :func:`_ask`. Only an organization the worker serves is acted on."""
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


# ---- the sample ------------------------------------------------------------------------------------------------------------------------------

async def compare(tx: Tx, org: str, ids: list[str], access: dict[str, Any]) -> tuple[set[str], str]:
    """In the caller's snapshot: the connectors whose answers differ between kg and the walk for the asked ids, and what the difference is (``over`` if kg admitted something the walk
    denied, else ``under``). Only nodes kg decides itself are compared: the rest is the walk's already."""
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


def maybe_sample(run: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]], db: KernelDB, org: str, ids: list[str], access: dict[str, Any]) -> None:
    """Repeat a share of the reader's answers by the walk, off the caller's path (a task with the caller's context, so its org)."""
    rate = sample_rate()
    if rate <= 0 or random.random() >= rate:
        return
    if len(_SAMPLES) >= MAX_SAMPLES:
        NET.inc("sample", "busy")
        return
    task = asyncio.get_running_loop().create_task(_sample(run, db, org, random.sample(ids, min(len(ids), SAMPLE_IDS)), access))
    _SAMPLES.add(task)
    task.add_done_callback(_SAMPLES.discard)


async def settle() -> None:
    """Wait for the samples in flight (tests and shutdown)."""
    while _SAMPLES:
        await asyncio.gather(*list(_SAMPLES), return_exceptions=True)


# ---- the periodic verify --------------------------------------------------------------------------------------------------------------------

async def _verify_one(db: KernelDB, org: str, app: str) -> str:
    """The connector against the graph in one snapshot: ``same``, ``differs``, or ``skipped`` (not served in that snapshot: a change waits in the log and kg is behind by design)."""
    async def go(tx: Tx) -> str:
        if not await drainer.serves(tx, org, app):
            return "skipped"
        await tx.fetchval("/* pipeshub:kg.net_verify_limit */ SELECT set_config('statement_timeout', $1, true)", VERIFY_TIMEOUT)
        res = await verify_app(tx, org, app) + await verify_principals(tx, org)
        return "same" if res.ok else "differs"
    with bind(drainer._ctx(org)):
        return await db.run(go, isolation="repeatable_read")


async def verify_due(db: KernelDB, org: str, due: dict[tuple[str, str], float], stop: asyncio.Event | None = None) -> int:
    """Verify the enforced connectors of ``org`` whose turn has come (``due``: when each is next verified), at most ``VERIFY_PER_PASS`` of them, the longest waiting first; a difference
    demotes. Returns the connectors verified. Never raises: a verify that fails changes nothing and is tried again next period."""
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
    turn = sorted((a for a in apps if due.get((org, a), 0.0) <= now), key=lambda a: due.get((org, a), 0.0))[:VERIFY_PER_PASS]
    done = 0
    for app in turn:
        if stop is not None and stop.is_set():
            break
        due[(org, app)] = time.monotonic() + every
        try:
            found = await _verify_one(db, org, app)
        except Exception:
            NET.inc("verify", "error")
            logger.warning("kg verify of connector %s failed; it is left as it is", app, exc_info=True)
            continue
        NET.inc("verify", found)
        if found == "differs":
            await demote(db, org, [app], source="verify", why="the copy differs from the graph")
        done += found != "skipped"
    return done
