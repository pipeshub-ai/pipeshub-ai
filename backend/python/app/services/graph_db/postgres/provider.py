"""``PostgresGraphProvider``: the knowledge graph on PostgreSQL (``DATA_STORE=postgres``).

Every node is a row of ``graph.nodes (org_id, coll, id, doc jsonb)`` and every edge a row of ``graph.edges``; ``coll`` is the collection
name the callers use. All reads and writes run in the ambient org (row-level security), through ``app.core.db``.

The provider is composed of one mixin per group of methods, each in its own module: ``identity`` (users, orgs, groups, roles), ``permissions`` (the access check), ``codegraph`` (code links and pipeline stage states) and ``core`` (generic documents and edges). A method no
mixin defines raises ``NotImplementedInPostgres``; ``unimplemented()`` lists them (the coverage list).
"""
from __future__ import annotations

import abc
import functools
from typing import TYPE_CHECKING, Any

from app.core.org_context import instrument_org_methods
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.postgres.base import NotImplementedInPostgres, PgBase
from app.services.graph_db.postgres.codegraph import CodeGraphMixin
from app.services.graph_db.postgres.core import CoreMixin
from app.services.graph_db.postgres.delete import DeleteMixin
from app.services.graph_db.postgres.entities import EntityMixin
from app.services.graph_db.postgres.entity_reads import EntityReadsMixin
from app.services.graph_db.postgres.hierarchy import HierarchyMixin, HubMixin
from app.services.graph_db.postgres.identity import IdentityMixin
from app.services.graph_db.postgres.kb import KbMixin
from app.services.graph_db.postgres.kb_listings import KbListingsMixin
from app.services.graph_db.postgres.kg.guard import instrument_writers
from app.services.graph_db.postgres.lookups import LookupsMixin
from app.services.graph_db.postgres.permissions import PermissionsMixin
from app.services.graph_db.postgres.records import RecordsMixin
from app.services.graph_db.postgres.sync_state import SyncStateMixin
from app.services.graph_db.postgres.trash import TrashMixin

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["NotImplementedInPostgres", "PostgresGraphProvider", "unimplemented"]

_MIXINS = (CodeGraphMixin, RecordsMixin, HierarchyMixin, HubMixin, TrashMixin, LookupsMixin, DeleteMixin, IdentityMixin, PermissionsMixin, KbMixin, KbListingsMixin, SyncStateMixin, EntityMixin, EntityReadsMixin, CoreMixin)
for _m in _MIXINS:
    instrument_org_methods(_m)  # the interface does it for methods a subclass defines itself, not for inherited ones
    instrument_writers(_m)  # the kg registry's guard names the provider method that writes the graph


class PostgresGraphProvider(*_MIXINS, PgBase, IGraphDBProvider):
    pass


_STUBBED: set[str] = set()


def unimplemented() -> list[str]:
    """Interface methods this provider still stubs, for the coverage test."""
    return sorted(_STUBBED)


def _stub(name: str) -> Callable:
    @functools.wraps(getattr(IGraphDBProvider, name))      # the interface's signature and docs: callers read parameters off the class
    async def stub(self: Any, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        raise NotImplementedInPostgres(f"PostgresGraphProvider.{name} is not implemented")
    stub.__isabstractmethod__ = False                       # wraps copied the abstract marker
    return stub


for _n in sorted(IGraphDBProvider.__abstractmethods__):
    if getattr(getattr(PostgresGraphProvider, _n), "__isabstractmethod__", False):
        setattr(PostgresGraphProvider, _n, _stub(_n))
        _STUBBED.add(_n)
abc.update_abstractmethods(PostgresGraphProvider)
