"""Which connectors' stamps may be used.

A connector's stamps are current only while ALL of these hold on its app document:

* its last scope back-fill finished (``done``) and wrote the current field version, under the current ACL scope generation;
* that pass started from the structure generation the connector holds NOW (`structure_generation.GENERATION_KEY`), read before the pass
  read any record: a structural change (a restriction, a move, a break or restore of inheritance, a delete) moves the generation in the
  transaction that writes it, so the connector is not current from the instant the change commits;
* the unsafe mark (a change that could not move the generation) is the one the pass read: a mark written since makes it not current;
* its pending-roots list (`pending.py`) reads: a value that does not parse is never "no roots";
* no write that could not be undone and whose connector was not known is in flight (`unclosed_writes`): its opening step has run and its closing step has not.

Anything that is missing or does not parse is *not* current. Everything else is read with the legacy filter and the post-check.

The answer is read from the app document at call time. **Do not cache it** beyond the search it serves, or invalidate the cache in the
same write that bumps the generation: a cached "current" is the window in which a revoked access still admits. A structural change only
makes a connector not current; it takes a new back-fill to make it current again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.services.permissions.pending import parse
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    current_scope_generation,
)
from app.services.permissions.structure_generation import (
    PENDING_KEY,
    UNSAFE_KEY,
    same_mark,
    structure_generation_of,
    unclosed_writes,
    unsafe_mark,
)
from app.services.permissions.structure_generation import whole_number as _int
from app.services.vector_db.scope_backfill import BACKFILL_KEYS, DONE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def app_is_current(app: Mapping[str, object] | None) -> bool:
    """Whether the stamps of the connector whose app document this is may be used now (see the module docstring)."""
    if not app:
        return False
    in_flight = unclosed_writes(app)
    if in_flight is None or in_flight > 0:           # a write that could not be undone has started and not written its closing mark
        return False
    keys = BACKFILL_KEYS
    try:
        parse(app.get(PENDING_KEY))
        held_mark, recorded_mark = unsafe_mark(app.get(UNSAFE_KEY)), unsafe_mark(app.get(keys["unsafe"]))
    except ValueError:  # PendingUnreadable is a ValueError
        return False
    # the pass covers the mark it read (it read the graph after the change that wrote it); a mark written since is another value
    if not same_mark(held_mark, recorded_mark):
        return False
    if app.get(keys["state"]) != DONE or _int(app.get(keys["version"])) != SCOPE_FIELDS_VERSION:
        return False
    acl = current_scope_generation()
    if acl < 0 or _int(app.get(keys["generation"])) != acl:
        return False
    stamped_from = _int(app.get(keys["structure_generation"]))
    held = structure_generation_of(app)
    return stamped_from is not None and held is not None and stamped_from == held


#: A running sync writes grants and memberships and moves the viewer-scope version only when it ends, so the cached scopes of a viewer who gained access
#: during it are stale: the connector is read with the legacy check until the sync is over.
RUNNING = frozenset({AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value})


def app_is_served(app: Mapping[str, object] | None) -> bool:
    """Whether a search may be decided by the stamps of this connector now: current, no pending root, and no sync running. A pending root hides its subtree
    from the filter and its container points are not read from their stamps, so the stamps alone would under-admit: the connector is read
    with the legacy check until the roots are rewritten. An unreadable list is not current already."""
    return app_is_current(app) and not parse((app or {}).get(PENDING_KEY)) and (app or {}).get("status") not in RUNNING


def app_is_exact(app: Mapping[str, object] | None) -> bool:
    """Whether the connector is served and its last pass left no stale or legacy point (`Progress.exact`, recorded with the pass's own keys, so it is read
    only beside the version, generation and structure generation `app_is_current` checks). Anything but the boolean ``True`` (missing, a number, a
    string) is not exact: a connector's points that no stamp decides are then the request's arms' to return, so it keeps today's over-fetch."""
    return app_is_served(app) and (app or {}).get(BACKFILL_KEYS["exact"]) is True


class StampCurrency:
    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

    async def served(self, connector_ids: Iterable[str]) -> frozenset[str]:
        return (await self.read(connector_ids))[0]

    async def read(self, connector_ids: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
        """The connectors among ``connector_ids`` whose stamps may decide a search now (`app_is_served`) and, of those, the exact ones (`app_is_exact`), from one batched read of their app
        documents, made for this call and kept by nobody: the answer moves in the transaction that moves the generation, so a cached one is
        a window in which a revoked access still admits. A read that fails, or a connector it does not return, is not served. Runs inside the
        organisation the connectors belong to."""
        ids = sorted(set(connector_ids))
        if not ids:
            return frozenset(), frozenset()
        try:
            found = await self._graph.get_nodes_by_field_in(CollectionNames.APPS.value, "_key", ids, raise_on_error=True)
        except Exception:
            return frozenset(), frozenset()
        docs = {key: d for d in found or [] if isinstance(d, dict) and (key := str(d.get("_key") or d.get("id"))) in ids}
        return frozenset(k for k, d in docs.items() if app_is_served(d)), frozenset(k for k, d in docs.items() if app_is_exact(d))

    async def current(self, connector_ids: Iterable[str]) -> frozenset[str]:
        """The connectors among ``connector_ids`` whose stamps are usable now. Runs inside the organisation the connectors belong to. A
        read that fails counts the connector as not current."""
        out: set[str] = set()
        for connector_id in connector_ids:
            try:
                app = await self._graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
            except Exception:
                continue
            if app_is_current(app):
                out.add(connector_id)
        return frozenset(out)
