"""The structure generation of a connector instance: a counter on its app document that moves whenever a write changes what the scope fields of
its vector points are made of (a hierarchy, group or inherit edge, a record's or group's access rule, model or place, a delete).

It is written inside the transaction that writes the structural change (`bump_generation_in`), so both commit or neither does. A stamp
computed at generation g is current only while the generation still reads g (`permissions.currency`). Without explicit transactions the
statements of a block commit one by one, so a block that cannot bump marks the connector unsafe instead (`UNSAFE_KEY`): its stamps are
never current until a pass over the whole connector clears the mark.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from time import (
    monotonic,  # (bound here: tests replace this module's `time` to move its wall clock)
)
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import (
    MissingOrgContext,
    SystemContextNotAllowed,
    current,
    current_or_system,
    for_org,
    system,
)
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.pending import (
    MAX_PENDING_ROOTS,
    PendingOverflow,
    PendingUnreadable,
    parse,
    with_roots,
    without_done,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Collection, Mapping

GENERATION_KEY = "scopeProjectionGeneration"
UNSAFE_KEY = "scopeProjectionUnsafe"
PENDING_KEY = "scopeProjectionPendingRoots"
OPEN_WRITES_KEY = "scopeProjectionOpenWrites"
#: How long a write that has opened a connector may run before the sweep reads it as dead. A write with long steps renews it before each one.
OPEN_WRITE_SECONDS = 600
#: Past its deadline a write is still given this long. The operating assumption the sweep's safety rests on: the skew between the store's
#: nodes, plus the delay between reading the store's clock and committing the lease written from it, stays within this. A lease write seen to
#: land later than `STORE_CLOCK_TIMEOUT_SECONDS` is measured again or marked `k` (best effort: a lost race leaves it as written, and the writer
#: renews before every long step and before the write that matters).
OPEN_WRITE_GRACE_SECONDS = 120
# A slower answer from the store's clock is not trusted: the time it gives is already that old when the lease is written or judged.
STORE_CLOCK_TIMEOUT_SECONDS = 5.0


def whole_number(value: object) -> int | None:
    """A whole number of zero or more, or None. A generation is stored as a number (the provider returns an int, or a float for a value an
    AQL increment computed: ``6.0`` is 6); a bool, a negative, a fraction, NaN, infinity and any string (``"5"``, ``" 5 "``, ``"1_0"``,
    ``"1e1"``) are not a generation."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and 0 <= value < 2**53:
        return int(value)
    return None


def new_unsafe_mark() -> int:
    """A fresh unsafe mark: a random positive whole number below 2**53 (exact in JSON and in the graph stores). A connector's mark is rewritten by
    every change that could not move the generation, and a pass records the mark it read, so a mark written after the read must be a different
    value (a constant would hide it). Only equality is used; a clock would give two writers in the same instant the same mark."""
    return secrets.randbits(52) + 1


OPENED_KEY = "scopeProjectionOpened"
CLOSED_KEY = "scopeProjectionClosed"


def unclosed_writes(app: Mapping[str, object] | None, *, allow_closed_ahead: bool = False) -> int | None:
    """How many writes that could not be undone and whose connector was not known (outside a transaction) have started and not yet written their
    closing mark on this connector: ``opened - closed``, two counters each incremented atomically (`increment_node_field`), so concurrent writes
    each count. 0 when neither exists. None when a counter does not read or ``closed`` is ahead of ``opened`` (a closing step with no opening step):
    unreadable is not 0. With ``allow_closed_ahead`` (the operator closes the writes) a ``closed`` ahead of ``opened`` is readable and negative: a write
    that was closed by hand finished afterwards and counted itself closed again."""
    app = app or {}
    opened = 0 if app.get(OPENED_KEY) is None else whole_number(app.get(OPENED_KEY))
    closed = 0 if app.get(CLOSED_KEY) is None else whole_number(app.get(CLOSED_KEY))
    if opened is None or closed is None or (closed > opened and not allow_closed_ahead):
        return None
    return opened - closed


def same_mark(a: object, b: object) -> bool:
    """Whether two marks read by `unsafe_mark` are the same mark: True is not the token 1, and 1 is not 1.0."""
    return type(a) is type(b) and a == b


def unsafe_mark(value: object) -> int | bool | None:
    """The mark a stored value stands for: None for none (absent, None, False), True for the mark older code wrote, the positive whole number
    `new_unsafe_mark` wrote. Anything else raises ValueError: a mark that cannot be read is not "no mark"."""
    if value is None or value is False:
        return None
    if value is True:
        return True
    number = whole_number(value)
    if number is None or number == 0:
        raise ValueError(f"not an unsafe mark: {value!r}")
    return number


def structure_generation_of(app: Mapping[str, object] | None) -> int | None:
    """The structure generation an app document holds: 0 when the connector's structure never changed (no field), None when the field is
    there and is not a whole number. Only an absent field is 0: an empty list, an empty string or ``False`` are not "never changed"."""
    value = (app or {}).get(GENERATION_KEY)
    return 0 if value is None else whole_number(value)


class ScopeGenerationError(RuntimeError):
    """The connector's structure generation could not be written: the stamps cannot be called current."""


class ScopeGenerationConflict(ScopeGenerationError):
    """Another transaction wrote the connector's generation first (an ArangoDB write-write conflict). The store reports it as
    transient, so the whole block is run again, which rereads the generation."""


async def bump_generation_in(tx_store: Any, connector_id: str, roots: Collection[str] | None = None) -> int:  # noqa: ANN401
    """Add one to the connector's generation inside the transaction that writes the structural change, in one statement under the
    app document's write lock (`increment_node_field`), so two transactions that both bump get two different, growing values. Raises when
    the write fails, which rolls the transaction back; a write-write conflict is `ScopeGenerationConflict`, which the caller retries.

    `roots`: the change reaches only the subtrees of these records; they are registered as pending after the bump. In a real transaction
    (``tx_store.explicit``) the app document stays locked until commit, so the read-modify-write of the list is safe and two syncs lose
    none. Without one the statements commit one by one, the lock is gone after the bump, and the list is written by compare-and-set
    instead (`_add_roots`). None, or more than `MAX_PENDING_ROOTS` roots, or a stored value that is unreadable: nothing is listed, and
    the bump alone leaves the connector not current until a full pass."""
    graph = tx_store.graph_provider
    try:
        new = await graph.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY, tx_store.txn)
        if new is not None and roots and not tx_store.explicit:
            await _add_roots(graph, connector_id, roots, new, tx_store.txn)
        elif new is not None and roots:
            app = await graph.get_document(connector_id, CollectionNames.APPS.value, transaction=tx_store.txn, raise_on_error=True) or {}
            try:
                value = with_roots(app.get(PENDING_KEY), roots, new)
            except (PendingOverflow, PendingUnreadable):
                return new
            if not await graph.update_node(connector_id, CollectionNames.APPS.value, {PENDING_KEY: value}, tx_store.txn):
                raise ScopeGenerationError(f"could not register the pending roots of {connector_id}")
    except Exception as exc:
        if graph.is_write_conflict(exc):
            raise ScopeGenerationConflict(f"another writer moved the structure generation of {connector_id} first") from exc
        raise
    if new is None:
        raise ScopeGenerationError(f"could not bump the structure generation of {connector_id}: no such connector")
    return new


async def _add_roots(graph_provider: Any, connector_id: str, roots: Collection[str], generation: int,  # noqa: ANN401
                     txn: str | None = None, *, attempts: int = 5) -> None:
    """Register ``roots`` at ``generation`` by compare-and-set on the stored string, retried: no registration is lost without a lock.
    Nothing is listed (the bump alone stands) past the cap or over an unreadable value."""
    for _ in range(attempts):
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, transaction=txn, raise_on_error=True) or {}
        raw = app.get(PENDING_KEY) or None
        try:
            value = with_roots(raw, roots, generation)
        except (PendingOverflow, PendingUnreadable):
            return
        if await graph_provider.update_node_fields_if_match(connector_id, CollectionNames.APPS.value, {PENDING_KEY: value}, {PENDING_KEY: raw}, txn):
            return
    raise ScopeGenerationError(f"could not register the pending roots of {connector_id}")


async def register_pending_roots(graph_provider: Any, connector_id: str, roots: Collection[str], *, attempts: int = 5) -> int:  # noqa: ANN401
    """`bump_generation_in` for a change made outside a transaction: bump, then add the roots by compare-and-set on the stored string
    (retried). A crash between the two leaves a bump with no root, which only costs a full pass. Raises when the bump or the
    registration cannot be written: a change nobody recorded leaves stamps that read as current."""
    new = await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY)
    if new is None:
        raise ScopeGenerationError(f"could not bump the structure generation of {connector_id}")
    await _add_roots(graph_provider, connector_id, roots, new, attempts=attempts)
    return new


class OpenWritesUnreadable(ValueError):
    """The stored list of open writes does not parse: it is never read as "none"."""


def parse_open_writes(raw: object) -> dict[str, dict[str, Any]]:
    """``{token: {"u": deadline in epoch seconds, "r": roots the write will register}}``. Absent is none; anything else that does not parse raises."""
    if raw is None or raw == "":
        return {}
    try:
        out: dict[str, dict[str, Any]] = {}
        for token, entry in json.loads(raw).items():  # type: ignore[arg-type]
            until = whole_number(entry["u"])
            roots = entry.get("r")
            if until is None or not (roots is None or (isinstance(roots, list) and all(isinstance(r, str) for r in roots))):
                raise ValueError
            out[str(token)] = {"u": until, **({"r": roots} if roots is not None else {}), **({"k": 1} if entry.get("k") else {})}
        return out
    except (ValueError, TypeError, AttributeError, KeyError):
        raise OpenWritesUnreadable(f"not an open-writes value: {raw!r:.80}") from None


def _dump_open_writes(held: Mapping[str, Any]) -> str | None:
    return json.dumps(dict(held), sort_keys=True, separators=(",", ":")) if held else None


async def _swap(graph_provider: Any, connector_id: str, updates: dict[str, Any], expected: dict[str, Any]) -> bool:  # noqa: ANN401
    """Compare-and-set on the app document; a write conflict is a lost race (False), anything else raises."""
    try:
        return bool(await graph_provider.update_node_fields_if_match(connector_id, CollectionNames.APPS.value, updates, expected))
    except Exception as exc:
        if getattr(graph_provider, "is_write_conflict", lambda _e: False)(exc):
            return False
        raise


async def _backoff(attempt: int) -> None:
    # Jitter on a growing ceiling (to 0.4 s): contenders on one document must not retry in step, and a crowd of them must thin out.
    await asyncio.sleep(secrets.randbelow(1 + min(400, 10 * 2 ** min(attempt, 6))) / 1000)


async def store_now(graph_provider: Any, clock: Callable[[], float] | None = None) -> float | None:  # noqa: ANN401
    """Now by the clock the writers and the sweep of a lease share: the graph store's (one node's clock: the Neo4j leader, the ArangoDB coordinator,
    the PostgreSQL primary, so as close to the other hosts' as NTP keeps them). ``clock`` (tests) replaces it. None, logged, when the store cannot
    say, does not answer within ``STORE_CLOCK_TIMEOUT_SECONDS``, or has no store clock: the sweep then recovers nothing that round and the writers
    mark their lease `k` (see `open_write`). It is not a single cluster clock: two ArangoDB coordinators or a Neo4j leader change can answer from
    clocks as far apart as NTP lets them drift, which ``OPEN_WRITE_GRACE_SECONDS`` must cover."""
    if clock is not None:
        return clock()
    try:
        return await asyncio.wait_for(graph_provider.store_time(), STORE_CLOCK_TIMEOUT_SECONDS)
    except Exception:  # TimeoutError included: a late answer is treated as no answer
        logging.getLogger(__name__).warning("the graph store's clock could not be read in %ss", STORE_CLOCK_TIMEOUT_SECONDS, exc_info=True)
        return None


async def _lease_deadline(graph_provider: Any, clock: Callable[[], float] | None, *, tries: int = 3) -> tuple[int, bool]:  # noqa: ANN401
    """``(deadline, from the store's clock)`` for a lease written now. A clock that cannot be read is tried again; after that the local clock is
    used and the caller marks the lease `k`, so that the sweep does not trust its deadline (see `recover_expired_writes`)."""
    for attempt in range(tries):
        if (now := await store_now(graph_provider, clock)) is not None:
            return int(now) + OPEN_WRITE_SECONDS, True
        await _backoff(attempt)
    return int(time.time()) + OPEN_WRITE_SECONDS, False


async def open_write(graph_provider: Any, connector_id: str, roots: Collection[str] | None = None, *,  # noqa: ANN401
                     attempts: int = 30, clock: Callable[[], float] | None = None) -> str | None:
    """Count one write that could not be undone as opened on the connector (`OPENED_KEY`) and record a lease for it, in one compare-and-set: a
    process that dies before it closes the write leaves a lease that runs out, and the sweep then does what the write would have done
    (`recover_expired_writes`) instead of waiting for an operator. ``roots``: the records the write will register once it is done (kept only up
    to `MAX_PENDING_ROOTS`; without them the recovery marks the connector unsafe). Returns the token for `close_write`; None when the stored
    list or counter cannot be read, or when the document is too contended for the compare-and-set, in which case the plain atomic counter is
    incremented as before (no lease: the operator view shows it). Contention alone never refuses the caller's write.
    Raises `ScopeGenerationError` when the connector does not exist or the counter cannot be written (the caller's write must not happen)."""
    for attempt in range(attempts):
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
        if not app:
            raise ScopeGenerationError(f"could not open a write on connector {connector_id}: no such connector")
        raw, opened = app.get(OPEN_WRITES_KEY) or None, app.get(OPENED_KEY)
        try:
            held = parse_open_writes(raw)
        except OpenWritesUnreadable:
            held = None
        if held is None or (opened is not None and whole_number(opened) is None):
            if await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, OPENED_KEY) is None:
                raise ScopeGenerationError(f"could not open a write on connector {connector_id}: no such connector")
            return None
        token = secrets.token_hex(8)
        # A store clock that cannot be read must not refuse the caller's write: the lease is then marked `k`, not trusted by the sweep.
        started = monotonic()
        until, by_store = await _lease_deadline(graph_provider, clock)
        entry: dict[str, Any] = {"u": until, **({} if by_store else {"k": 1})}
        if roots is not None and len(roots) <= MAX_PENDING_ROOTS:
            entry["r"] = sorted(roots)
        if await _swap(graph_provider, connector_id, {OPEN_WRITES_KEY: _dump_open_writes({**held, token: entry}), OPENED_KEY: (whole_number(opened) or 0) + 1},
                       {OPEN_WRITES_KEY: raw, OPENED_KEY: opened}):
            if by_store and monotonic() - started > STORE_CLOCK_TIMEOUT_SECONDS:
                # the write landed late, so its deadline is that much shorter than a lease: measure it again from now
                try:
                    return await renew_write(graph_provider, connector_id, token, roots, clock=clock, again=False)
                except BaseException:
                    # nobody holds the token yet, so close its lease here, or the connector stays not current until the sweep
                    await asyncio.shield(close_write(graph_provider, connector_id, token))
                    raise
            return token
        await _backoff(attempt)
    if await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, OPENED_KEY) is None:
        raise ScopeGenerationError(f"could not open a write on connector {connector_id}: no such connector")
    return None


async def close_write(graph_provider: Any, connector_id: str, token: str | None, *, attempts: int = 30) -> bool:  # noqa: ANN401
    """Count the write as closed and drop its lease, in one compare-and-set. Retried, because it is idempotent: a lease that is gone was closed
    already, by this call or by the sweep (which counted it), and is not counted twice. A lease that is gone because the sweep read this write as
    dead means the sweep registered its roots at a time that may be before the write landed: the connector is marked unsafe (a full pass), not
    left looking fine. False (the connector stays not current until its lease runs out and the sweep closes it) when it cannot be written, which
    contention alone does only after `attempts` tries with a growing wait."""
    if token is None:  # opened without a lease
        try:
            return await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, CLOSED_KEY) is not None
        except Exception:
            return False
    for attempt in range(attempts):
        try:
            app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
            raw, closed = app.get(OPEN_WRITES_KEY) or None, app.get(CLOSED_KEY)
            held = parse_open_writes(raw)
            if token not in held:
                return await graph_provider.update_node(connector_id, CollectionNames.APPS.value, {UNSAFE_KEY: new_unsafe_mark()}) is not False
            del held[token]
            if await _swap(graph_provider, connector_id, {OPEN_WRITES_KEY: _dump_open_writes(held), CLOSED_KEY: (whole_number(closed) or 0) + 1},
                           {OPEN_WRITES_KEY: raw, CLOSED_KEY: closed}):
                return True
        except Exception:
            pass
        await _backoff(attempt)
    return False


async def renew_write(graph_provider: Any, connector_id: str, token: str | None, roots: Collection[str] | None = None, *,  # noqa: ANN401
                      attempts: int = 10, clock: Callable[[], float] | None = None, again: bool = True) -> str | None:
    """Push the deadline of a write that is still running, right before a long step or the write that matters. When the lease is gone (the sweep
    read the write as dead and closed it) the write is opened again with its roots, so a registration happens again for what lands from here on
    instead of the write going on unfenced; the new token is returned. A renewal that cannot be written leaves the old lease running."""
    if token is None:
        return None
    distrust, attempt = False, 0
    while attempt < attempts:      # (a late write's follow-up is not an attempt: it must happen even after the last one)
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
        raw = app.get(OPEN_WRITES_KEY) or None
        try:
            held = parse_open_writes(raw)
        except OpenWritesUnreadable:
            return token
        if token not in held:
            return await open_write(graph_provider, connector_id, roots, clock=clock)
        started = monotonic()
        until, by_store = await _lease_deadline(graph_provider, clock)
        by_store = by_store and not distrust
        # (never shortened by a clock that is not the store's: a host far behind it would write a deadline that is already past)
        kept = {f: v for f, v in held[token].items() if f != "k"}
        held[token] = {**kept, "u": until if by_store else max(held[token]["u"], until), **({} if by_store else {"k": 1})}
        if await _swap(graph_provider, connector_id, {OPEN_WRITES_KEY: _dump_open_writes(held)}, {OPEN_WRITES_KEY: raw}):
            if by_store and monotonic() - started > STORE_CLOCK_TIMEOUT_SECONDS:
                # landed late: once more from now; late again, the lease is marked `k` (a mark no delay can make wrong), so a store that
                # stays slow neither keeps the writer here nor leaves a deadline the sweep would trust
                again, distrust = False, not again
                continue
            return token
        await _backoff(attempt)
        attempt += 1
    return token


async def recover_expired_writes(graph_provider: Any, connector_id: str, *, clock: Callable[[], float] | None = None) -> int:  # noqa: ANN401
    """Close the writes of the connector whose lease ran out (past `OPEN_WRITE_GRACE_SECONDS`): the process that opened them is gone. When every
    one of them recorded its roots they are registered, as the write would have done after itself (the subtree is hidden and the engine
    rewrites it); otherwise the connector is marked unsafe, as `--close-open-mark` does (a full pass). The registration comes first and the
    close is one compare-and-set that also counts them, so a crash in between repeats the registration (harmless) and never loses it, and a
    write that finishes late finds its lease gone and registers its own roots again. Returns how many writes it closed (0 on a lost race).

    Deadlines are written and judged by the store's clock (`store_now`), so a host with a wrong clock can neither recover a live write nor keep a dead
    one. When the store's clock cannot be read nothing is recovered (0): the connector stays not current and the operator view shows it. A lease
    its writer could not time by the store's clock (marked `k`) is never read as dead on its own deadline; the first sweep that sees it gives it a
    full lease by the store's clock, so a live write is recovered only after a lease the store has measured, and a dead one still is."""
    app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
    raw, closed = app.get(OPEN_WRITES_KEY) or None, app.get(CLOSED_KEY)
    held = parse_open_writes(raw)
    started = monotonic()
    now = await store_now(graph_provider, clock)
    if now is None:
        return 0
    cutoff = now - OPEN_WRITE_GRACE_SECONDS
    relet = {t for t, e in held.items() if e.get("k")}
    dead = {t: e for t, e in held.items() if not e.get("k") and e["u"] <= cutoff}
    released = {t: ({**{f: v for f, v in e.items() if f != "k"}, "u": int(now) + OPEN_WRITE_SECONDS} if e.get("k") else e) for t, e in held.items()}
    if not dead or (closed is not None and whole_number(closed) is None):
        if relet and await _swap(graph_provider, connector_id, {OPEN_WRITES_KEY: _dump_open_writes(released)}, {OPEN_WRITES_KEY: raw}):  # (lost: next sweep)
            await _unless_on_time(graph_provider, connector_id, relet, started)      # (the dead leases stay: with the counter unreadable they are the only record)
        return 0
    kept = {t: e for t, e in released.items() if t not in dead}
    updates: dict[str, Any] = {OPEN_WRITES_KEY: _dump_open_writes(kept), CLOSED_KEY: (whole_number(closed) or 0) + len(dead)}
    if all("r" in e for e in dead.values()):
        if roots := sorted({r for e in dead.values() for r in e["r"]}):
            await register_pending_roots(graph_provider, connector_id, roots)
    else:
        updates[UNSAFE_KEY] = new_unsafe_mark()
    if not await _swap(graph_provider, connector_id, updates, {OPEN_WRITES_KEY: raw, CLOSED_KEY: closed}):
        return 0
    await _unless_on_time(graph_provider, connector_id, relet, started)
    return len(dead)


async def _unless_on_time(graph_provider: Any, connector_id: str, relet: Collection[str], started: float) -> None:  # noqa: ANN401
    """A sweep that re-leased `k` leases by the store's clock and wrote them late wrote deadlines that much shorter: mark them `k` again (a mark no
    delay can make wrong) for the next sweep. Once: a lost race means their writer or another sweep has written them since."""
    if not relet or monotonic() - started <= STORE_CLOCK_TIMEOUT_SECONDS:
        return
    try:
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
        raw = app.get(OPEN_WRITES_KEY) or None
        held = parse_open_writes(raw)
        if any(t in held and not held[t].get("k") for t in relet):
            await _swap(graph_provider, connector_id, {OPEN_WRITES_KEY: _dump_open_writes({t: {**e, "k": 1} if t in relet else e for t, e in held.items()})},
                        {OPEN_WRITES_KEY: raw})
    except Exception:
        logging.getLogger(__name__).warning("could not mark the late re-leases of connector %s unknown; they run on the sweep's deadline", connector_id, exc_info=True)


class OpenWrites:
    """What `leaving_vrid` hands the body of the fence: `renew()` before a long step and before the write that matters."""

    def __init__(self, graph_provider: Any, tokens: dict[str, str | None], roots: Mapping[str, Collection[str]], log: logging.Logger) -> None:  # noqa: ANN401
        self._graph, self._tokens, self._roots, self._log = graph_provider, tokens, roots, log

    async def renew(self) -> None:
        for connector_id in sorted(self._tokens):
            try:
                self._tokens[connector_id] = await renew_write(self._graph, connector_id, self._tokens[connector_id], self._roots.get(connector_id))
            except Exception:
                self._log.warning("could not renew the lease of the write on connector %s; it runs out on its own deadline", connector_id, exc_info=True)


@contextlib.asynccontextmanager
async def leaving_vrid(graph_provider: Any, record_key: str, vrid: str, logger: logging.Logger | None = None) -> AsyncIterator[OpenWrites | None]:  # noqa: ANN401
    """Around the write that makes a record stop standing for virtual record ``vrid``. The points of ``vrid`` keep the scope of the record
    that left until they are stamped again, so each record that still stands for it is registered as a pending root of its connector
    after the write. Before the write each of those connectors is opened (`OPENED_KEY`), and closed only once its roots are registered
    (`CLOSED_KEY`): a process that dies between the write and the registration leaves the connector with an unclosed write, which is not
    current, so no crash point has the write durable and nothing fencing it. With no other record the points have no owner and nothing is
    done. If the sharers cannot be read, every connector of the organisation is marked before the write and again after it (`note_*`).
    The mark before the write must land, or this raises and the write does not happen (the event is retried); the one after is logged at
    error when it fails, because the write is done. That path has the crash window only when a pass also finishes between the two marks.
    Raises when an opening cannot be written (after closing the connectors already opened) or a registration cannot, after trying every
    connector; a connector whose registration failed is not closed, which keeps it not current.

    A process that dies inside the window leaves the write opened with a lease (`open_write`); once the lease has run out the sweep registers the
    roots itself and closes the write (`recover_expired_writes`), which is what recovers it without an operator. Until then the connector is not
    current. A body with long steps calls ``renew()`` on the handle this yields before each of them and before the graph write."""
    log = logger or logging.getLogger(__name__)
    by_connector: dict[str, list[str]] = {}
    try:
        for doc in await graph_provider.get_nodes_by_field_in(CollectionNames.RECORDS.value, "virtualRecordId", [vrid], raise_on_error=True):
            key = str(doc.get("_key") or doc.get("id") or "")
            if key and key != record_key and doc.get("connectorId"):
                by_connector.setdefault(str(doc["connectorId"]), []).append(key)
    except Exception:
        log.exception("could not read the records sharing virtual record %s: marking the organisation's connectors instead", vrid)
        await _mark_organisation(graph_provider, log, before_write=True)
        try:
            yield None
        finally:
            await _mark_organisation(graph_provider, log, before_write=False)
        return
    connectors = sorted(by_connector)  # one order for every writer
    opened: dict[str, str | None] = {}
    try:
        for connector_id in connectors:
            opened[connector_id] = await open_write(graph_provider, connector_id, by_connector[connector_id])
    except BaseException:
        for connector_id, token in opened.items():  # the write will not happen: do not leave the ones opened so
            await _close(graph_provider, connector_id, token, log)
        raise
    first: BaseException | None = None
    try:
        yield OpenWrites(graph_provider, opened, by_connector, log)
    finally:  # a write that raised may still have landed: its registration is harmless when it did not
        for connector_id in connectors:
            try:
                await register_pending_roots(graph_provider, connector_id, by_connector[connector_id])
            except Exception as exc:
                log.error("could not register the pending roots of connector %s: it stays opened, so not current", connector_id)
                first = first or exc
                continue
            await _close(graph_provider, connector_id, opened[connector_id], log)
        if first is not None:
            raise first


async def _close(graph_provider: Any, connector_id: str, token: str | None, log: logging.Logger) -> None:  # noqa: ANN401
    """Close one write (`close_write`). A close that did not land leaves the connector opened (not current) until its lease runs out and the
    sweep closes it, or an operator does; logged."""
    if not await close_write(graph_provider, connector_id, token):
        log.error("could not close the write on connector %s: it stays not current until its lease runs out (the sweep closes it) or the back-fill is run with --close-open-mark", connector_id)


async def _mark_organisation(graph_provider: Any, log: logging.Logger, *, before_write: bool) -> None:  # noqa: ANN401
    """Mark every connector of the current organisation. Before the write a missing organisation or a mark that did not land raises, so the
    write does not happen; after it the failure is logged at error with the organisation."""
    try:
        org_id = current().org_id
    except (MissingOrgContext, SystemContextNotAllowed):
        if before_write:
            raise ScopeGenerationError("no organisation is bound, so the connectors sharing the virtual record cannot be marked") from None
        log.error("no organisation bound: the connectors sharing a virtual record were not marked after the write")
        return
    if not await note_org_structure_change(graph_provider, org_id, log):
        if before_write:
            raise ScopeGenerationError(f"could not mark the connectors of organisation {org_id} before the write")
        log.error("could not mark the connectors of organisation %s after the write: their stamps may read as current", org_id)


async def clear_pending_covered(graph_provider: Any, connector_id: str, done_at: int, *, attempts: int = 5) -> None:  # noqa: ANN401
    """Take off the list the roots a full pass covered: the pass read the graph from structure generation ``done_at``, so every root
    registered at or before it is in the stamps it wrote. Compare-and-set on the stored string: a root registered again meanwhile
    (a newer generation) stays. An unreadable value is dropped only while the generation is still ``done_at``, when nothing can
    have been registered after the pass began. Raises when it cannot be written, so the pass is not reported as finished."""
    for _ in range(attempts):
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
        raw = app.get(PENDING_KEY) or None
        if raw is None:
            return
        try:
            left = without_done(raw, dict.fromkeys(parse(raw), done_at))
        except PendingUnreadable:
            if structure_generation_of(app) != done_at:
                return
            left = None
        if left == raw:
            return
        if await graph_provider.update_node_fields_if_match(connector_id, CollectionNames.APPS.value, {PENDING_KEY: left}, {PENDING_KEY: raw}):
            return
    raise ScopeGenerationError(f"could not take the covered roots off the pending list of {connector_id}")


async def mark_scope_dirty(graph_provider: Any, connector_id: str | None, *, attempts: int = 3) -> None:  # noqa: ANN401
    """For a structural write made outside `GraphTransactionStore` (a provider method called directly): once it is done, move the
    connector's generation on its own, outside any transaction (one more than before, in one statement). Retried; if the bump
    cannot be written the connector is marked unsafe instead, and only if that fails too does it raise, because a change nobody
    recorded leaves stamps that read as current. No connector (the write touched none): nothing to move."""
    if not connector_id:
        return
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            if await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY) is None:
                raise ScopeGenerationError(f"could not bump the structure generation of {connector_id}")
            return
        except Exception as exc:
            last = exc
            await asyncio.sleep(0.2 * (attempt + 1))
    logging.getLogger(__name__).error("structure generation of %s not moved (%s): marking it unsafe", connector_id, type(last).__name__)
    try:
        marked = await graph_provider.update_node(connector_id, CollectionNames.APPS.value, {UNSAFE_KEY: new_unsafe_mark()}) is not False
    except Exception as exc:
        marked, last = False, exc
    if not marked:
        logging.getLogger(__name__).error(
            "connector %s: neither the structure generation nor the unsafe mark could be written; its stamps may read as current", connector_id)
        raise ScopeGenerationError(f"could neither bump nor mark unsafe the structure generation of {connector_id}") from last


async def note_structure_change(graph_provider: Any, connector_id: str | None, logger: logging.Logger | None = None) -> bool:  # noqa: ANN401
    """`mark_scope_dirty` for a caller that must not fail on it (its structural write is done): only while the scope fields are on, and a
    connector it cannot record is logged at error (`mark_scope_dirty` has already tried the unsafe mark). Returns False when the change
    could not be recorded, so a caller that must not finish without it (a migration) can stay pending."""
    if not scope_fields_enabled():
        return True
    try:
        await mark_scope_dirty(graph_provider, connector_id)
    except Exception:
        (logger or logging.getLogger(__name__)).exception("could not record the structure change of connector %s", connector_id)
        return False
    return True


async def note_org_structure_change(graph_provider: Any, org_id: str | None, logger: logging.Logger | None = None) -> bool:  # noqa: ANN401
    """The same for a write that touched an unknown set of the organisation's connectors (a node purge, a migration): every connector of
    the organisation, or of every organisation when ``org_id`` is None. Returns False when the organisations or their connectors could not
    be listed, or a connector could not be recorded: the change is then not recorded everywhere (logged), and a migration stays pending."""
    if not scope_fields_enabled():
        return True
    log = logger or logging.getLogger(__name__)
    try:
        try:
            current_or_system()
            enumeration: contextlib.AbstractContextManager = contextlib.nullcontext()
        except MissingOrgContext:  # a job with no org bound lists the organisations in an explicit cross-org scope
            enumeration = system("scope_structure_change")
        with enumeration:
            orgs = [org_id] if org_id else [str(o.get("_key") or o.get("id")) for o in await graph_provider.get_all_orgs(active=False, raise_on_error=True)]
    except Exception:
        log.exception("could not list the organisations whose structure changed (organisation %s)", org_id or "all")
        return False
    recorded = True
    for org in orgs:
        try:
            with for_org(org, job="scope_structure_change"):  # the apps of one organisation are read, and marked, inside it
                for app in await graph_provider.get_org_apps(org, active_only=False, raise_on_error=True) or []:
                    recorded &= await note_structure_change(graph_provider, str(app.get("_key") or app.get("id")), log)
        except Exception:
            log.exception("could not list the connectors whose structure changed (organisation %s)", org)
            recorded = False
    return recorded
