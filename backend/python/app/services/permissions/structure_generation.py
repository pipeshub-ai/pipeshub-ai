"""The structure generation of a connector instance: a counter on its app document that moves whenever a write changes what the scope fields of
its vector points are made of (a hierarchy, group or inherit edge, a record's or group's access rule, model or place, a delete).

It is written inside the transaction that writes the structural change (`bump_generation_in`), so both commit or neither does. A stamp
computed at generation g is current only while the generation still reads g; nothing reads it yet. Without explicit transactions the
statements of a block commit one by one, so a block that cannot bump marks the connector unsafe instead (`UNSAFE_KEY`): its stamps are
never current until a pass over the whole connector clears the mark.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import MissingOrgContext, current_or_system, for_org, system
from app.services.permissions.mode import scope_fields_enabled

if TYPE_CHECKING:
    from collections.abc import Mapping

GENERATION_KEY = "scopeProjectionGeneration"
UNSAFE_KEY = "scopeProjectionUnsafe"


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
    """A fresh unsafe mark: microseconds since the epoch. A connector's mark is rewritten by every change that could not move the generation, and
    a pass records the mark it read, so a mark written after the read is a different value (a constant would hide it)."""
    return time.time_ns() // 1000


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


async def bump_generation_in(tx_store: Any, connector_id: str) -> int:  # noqa: ANN401
    """Add one to the connector's generation inside the transaction that writes the structural change, in one statement under the
    app document's write lock (`increment_node_field`), so two transactions that both bump get two different, growing values. Raises when
    the write fails, which rolls the transaction back; a write-write conflict is `ScopeGenerationConflict`, which the caller retries."""
    graph = tx_store.graph_provider
    try:
        new = await graph.increment_node_field(connector_id, CollectionNames.APPS.value, GENERATION_KEY, tx_store.txn)
    except Exception as exc:
        if graph.is_write_conflict(exc):
            raise ScopeGenerationConflict(f"another writer moved the structure generation of {connector_id} first") from exc
        raise
    if new is None:
        raise ScopeGenerationError(f"could not bump the structure generation of {connector_id}: no such connector")
    return new


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
