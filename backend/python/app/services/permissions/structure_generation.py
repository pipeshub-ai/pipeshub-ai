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
import logging
import secrets
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
    PendingOverflow,
    PendingUnreadable,
    parse,
    with_roots,
    without_done,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping

GENERATION_KEY = "scopeProjectionGeneration"
UNSAFE_KEY = "scopeProjectionUnsafe"
PENDING_KEY = "scopeProjectionPendingRoots"


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

    `roots`: the change reaches only the subtrees of these records; they are registered as pending in the same transaction, after the
    lock is taken, so a registration read-modifies-writes the latest value and two syncs lose none. None, or more than
    `MAX_PENDING_ROOTS` roots, or a stored value that is unreadable: nothing is listed, and the bump alone leaves the connector not
    current until a full pass."""
    graph = tx_store.graph_provider
    try:
        new = await graph.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY, tx_store.txn)
        if new is not None and roots:
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


async def register_pending_roots(graph_provider: Any, connector_id: str, roots: Collection[str], *, attempts: int = 5) -> int:  # noqa: ANN401
    """`bump_generation_in` for a change made outside a transaction: bump, then add the roots by compare-and-set on the stored string
    (retried). A crash between the two leaves a bump with no root, which only costs a full pass. Raises when the bump or the
    registration cannot be written: a change nobody recorded leaves stamps that read as current."""
    new = await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY)
    if new is None:
        raise ScopeGenerationError(f"could not bump the structure generation of {connector_id}")
    for _ in range(attempts):
        app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True) or {}
        raw = app.get(PENDING_KEY) or None
        try:
            value = with_roots(raw, roots, new)
        except (PendingOverflow, PendingUnreadable):
            return new
        if await graph_provider.update_node_fields_if_match(connector_id, CollectionNames.APPS.value, {PENDING_KEY: value}, {PENDING_KEY: raw}):
            return new
    raise ScopeGenerationError(f"could not register the pending roots of {connector_id}")


@contextlib.asynccontextmanager
async def leaving_vrid(graph_provider: Any, record_key: str, vrid: str, logger: logging.Logger | None = None) -> AsyncIterator[None]:  # noqa: ANN401
    """Around the write that makes a record stop standing for virtual record ``vrid``. The points of ``vrid`` keep the scope of the record
    that left until they are stamped again, so each record that still stands for it is registered as a pending root of its connector
    after the write. Before the write each of those connectors is opened (`OPENED_KEY`), and closed only once its roots are registered
    (`CLOSED_KEY`): a process that dies between the write and the registration leaves the connector with an unclosed write, which is not
    current, so no crash point has the write durable and nothing fencing it. With no other record the points have no owner and nothing is
    done. If the sharers cannot be read, every connector of the organisation is marked before the write and again after it (`note_*`,
    logged and not raised when a mark fails), because indexing must not fail on a read; that path has the crash window only when a pass
    also finishes between the two marks. Raises when an opening or a registration cannot be written."""
    log = logger or logging.getLogger(__name__)
    by_connector: dict[str, list[str]] = {}
    try:
        for doc in await graph_provider.get_nodes_by_field_in(CollectionNames.RECORDS.value, "virtualRecordId", [vrid], raise_on_error=True):
            key = str(doc.get("_key") or doc.get("id") or "")
            if key and key != record_key and doc.get("connectorId"):
                by_connector.setdefault(str(doc["connectorId"]), []).append(key)
    except Exception:
        log.exception("could not read the records sharing virtual record %s: marking the organisation's connectors instead", vrid)
        await _mark_organisation(graph_provider, log)
        try:
            yield
        finally:
            await _mark_organisation(graph_provider, log)
        return
    connectors = sorted(by_connector)  # one order for every writer
    for connector_id in connectors:
        if await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, OPENED_KEY) is None:
            raise ScopeGenerationError(f"could not open the write that moves {record_key} off virtual record {vrid}: no connector {connector_id}")
    try:
        yield
    finally:  # a write that raised may still have landed: its registration is harmless when it did not
        for connector_id in connectors:
            await register_pending_roots(graph_provider, connector_id, by_connector[connector_id])
            if await graph_provider.increment_node_field(connector_id, CollectionNames.APPS.value, CLOSED_KEY) is None:
                raise ScopeGenerationError(f"could not close the write on connector {connector_id}")


async def _mark_organisation(graph_provider: Any, log: logging.Logger) -> None:  # noqa: ANN401
    try:
        org_id = current().org_id
    except (MissingOrgContext, SystemContextNotAllowed):
        log.error("no organisation bound: the connectors sharing a virtual record were not marked")
        return
    await note_org_structure_change(graph_provider, org_id, log)


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
