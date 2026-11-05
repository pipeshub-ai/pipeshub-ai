"""Which connectors' stamps may be used.

A connector's stamps are current only while ALL of these hold on its app document:

* its last scope back-fill finished (``done``) and wrote the current field version, under the current ACL scope generation;
* that pass started from the structure generation the connector holds NOW (`structure_generation.GENERATION_KEY`), read before the pass
  read any record: a structural change (a restriction, a move, a break or restore of inheritance, a delete) moves the generation in the
  transaction that writes it, so the connector is not current from the instant the change commits;
* nothing marked the connector unsafe (a change that could not move the generation).

Anything that is missing or does not parse is *not* current. Everything else is read with the legacy filter and the post-check.

The answer is read from the app document at call time. **Do not cache it** beyond the search it serves, or invalidate the cache in the
same write that bumps the generation: a cached "current" is the window in which a revoked access still admits. A structural change only
makes a connector not current; it takes a new back-fill to make it current again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.services.permissions.scope_fields import SCOPE_FIELDS_VERSION, current_scope_generation
from app.services.permissions.structure_generation import GENERATION_KEY, UNSAFE_KEY, structure_generation_of, whole_number as _int
from app.services.vector_db.scope_backfill import BACKFILL_KEYS, DONE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def app_is_current(app: Mapping[str, object] | None) -> bool:
    """Whether the stamps of the connector whose app document this is may be used now (see the module docstring)."""
    if not app or app.get(UNSAFE_KEY):
        return False
    keys = BACKFILL_KEYS
    if app.get(keys["state"]) != DONE or _int(app.get(keys["version"])) != SCOPE_FIELDS_VERSION:
        return False
    acl = current_scope_generation()
    if acl < 0 or _int(app.get(keys["generation"])) != acl:
        return False
    stamped_from = _int(app.get(keys["structure_generation"]))
    held = structure_generation_of(app)
    return stamped_from is not None and held is not None and stamped_from == held


class StampCurrency:
    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

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
