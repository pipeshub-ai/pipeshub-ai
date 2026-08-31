"""One collection per connector type: ``google_drive_records``, ``slack_records``.

Opt-in via ``VECTOR_COLLECTION_STRATEGY=per_connector_type``. The default
remains ``single``, which is what every provider recommends and what most
deployments should stay on.

When this is worth choosing
---------------------------
Every search already carries a ``virtualRecordId IN [...]`` filter built from
the user's accessible records. On a large single collection that filter is
evaluated against every point in the corpus. Partitioning by connector type
gives each query a smaller collection to work in, and it aligns with how people
actually scope searches ("find it in Drive"). It also lets one connector type's
data be dropped, re-indexed, or migrated without touching the rest.

The cost is fan-out: see ``resolve_read_collections``.

Bounds
------
The number of collections is bounded by the number of connector *types* a
deployment has actually indexed — a handful in practice, ~30 at the ceiling
even if every supported connector is in use. That sits well inside every
provider's practical limit (the tightest is OpenSearch at ~200), which is what
makes this safe to offer where a per-org or per-record strategy would not be.

Switching to it
---------------
Not a config edit on a live deployment. Points already sit in ``records``,
which this strategy never resolves, so the services fail fast on startup and
the operator runs the embedding-rebuild procedure. See ``strategy_resolver``.
"""

from collections.abc import Sequence

from app.services.vector_db.collection_manifest import ManagedCollection
from app.services.vector_db.collections import CollectionType, sanitize_collection_name
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.strategy import (
    CollectionStrategy,
    ContextAxis,
    DeleteAction,
    DeleteContext,
    DeleteScope,
    QueryContext,
    RecordContext,
)


class PerConnectorTypeStrategy(CollectionStrategy):
    """Groups records by ``connectorName`` — the type, not the instance.

    ``connectorName`` is the ``Connectors`` enum value (``GOOGLE_DRIVE``,
    ``SLACK``, ``KNOWLEDGE_BASE``); ``connectorId`` is a per-instance UUID.
    Grouping by the type means two Google Drive instances share one collection,
    which is the point: the count stays bounded by how many kinds of source a
    deployment has, not by how many connections it has configured.
    """

    @staticmethod
    def _name(connector_name: str, collection_type: CollectionType) -> str:
        """Compose and normalize in one place, for all three resolutions.

        Connector enum values carry spaces and uppercase (``SHAREPOINT
        ONLINE``, ``DRIVE WORKSPACE``). The registry sanitizes whatever a
        strategy returns, so leaving that to it would still work — but the read
        and delete methods would then emit a different spelling from the write
        method, and anything comparing those strings directly would be
        comparing normalized against raw. Normalizing here keeps the strategy
        self-consistent; the registry's pass over it is idempotent.
        """
        return sanitize_collection_name(f"{connector_name}_{collection_type.value}")

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return self._name(ctx.connector_name or "", ctx.collection_type)

    def resolve_read_collections(
        self, ctx: QueryContext, managed: Sequence[ManagedCollection]
    ) -> list[str]:
        """Narrow to the searcher's connector types when the caller supplies them.

        The query path fills ``accessible_connector_names`` from
        ``IGraphDBProvider.get_accessible_connector_types`` — see
        ``read_narrowing_axes``, which is how it knows to ask — so a user who
        can only reach Slack queries one collection instead of every managed
        one.

        The fallback matters as much as the narrowing. Anything that cannot
        establish those types (an older caller, a graph hiccup, a search with
        no user) leaves them unset, and the honest answer is then every managed
        collection of this type: each is queried for the full limit and the
        ranked lists are fused, so a wider search costs round trips but never
        recall. Guessing a narrower set would silently hide data instead.
        """
        if ctx.accessible_connector_names:
            return [
                self._name(name, ctx.collection_type)
                for name in ctx.accessible_connector_names
            ]
        return [
            entry.name
            for entry in managed
            if entry.collection_type == ctx.collection_type.value
        ]

    def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        """Always a filtered delete, never a collection drop.

        Deleting one connector *instance* must not drop the collection its
        type shares with every other instance of that type — a second Google
        Drive connection would lose its vectors. Proving no other instance
        remains needs a graph lookup that a strategy may not do, and the
        registry only supplies that fact when a deployment wires a liveness
        probe; none does today, so a drop asked for here would be downgraded
        anyway.

        The saving would be small in any case: an empty collection costs
        little, and the filtered delete runs through the membership-aware VRID
        path, which is what keeps points shared with a live connector through
        deduplication from being taken with it.
        """
        return DeleteScope(
            action=DeleteAction.FILTERED_DELETE,
            collection_names=[self._name(ctx.connector_name or "", ctx.collection_type)],
            filter_field=CONNECTOR_IDS_FIELD,
            # No connector id yields no predicate: the executor refuses a
            # filterless delete rather than emptying the whole collection.
            filter_values=[ctx.connector_id] if ctx.connector_id else None,
        )

    def strategy_name(self) -> str:
        return "per_connector_type"

    @property
    def required_axes(self) -> frozenset[ContextAxis]:
        return frozenset({ContextAxis.CONNECTOR_NAME})

    @property
    def read_narrowing_axes(self) -> frozenset[ContextAxis]:
        """Knowing the searcher's connector types turns a fan-out over every
        managed collection into one query per type they can actually reach."""
        return frozenset({ContextAxis.CONNECTOR_NAME})
