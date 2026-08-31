"""Default collection strategy: one collection per CollectionType.

Matches every vector DB provider's own recommendation (see the plan's
provider research) and is byte-identical to PipesHub's existing behaviour:
one "records" collection, isolation done entirely through payload filters
(``connectorIds``, ``orgId``, ``virtualRecordId``) rather than separate
collections.
"""

from collections.abc import Sequence

from app.services.vector_db.collection_manifest import ManagedCollection
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.strategy import (
    CollectionStrategy,
    DeleteAction,
    DeleteContext,
    DeleteScope,
    QueryContext,
    RecordContext,
)


class SingleCollectionStrategy(CollectionStrategy):
    """One collection per CollectionType: 'records' today, 'entities' when it lands.

    ``required_axes`` stays empty (inherited): the name depends on nothing but
    the collection type, which is what lets org-agnostic callers resolve it.
    """

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return ctx.collection_type.value

    def resolve_read_collections(
        self, ctx: QueryContext, managed: Sequence[ManagedCollection]
    ) -> list[str]:
        return [ctx.collection_type.value]

    def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        return DeleteScope(
            action=DeleteAction.FILTERED_DELETE,
            collection_names=[ctx.collection_type.value],
            filter_field=CONNECTOR_IDS_FIELD,
            # A missing connector id yields no predicate rather than a
            # ``[None]`` one: the executor refuses a filterless delete, which
            # is the only safe reading of "remove this connector" when we
            # cannot say which connector.
            filter_values=[ctx.connector_id] if ctx.connector_id else None,
        )

    def strategy_name(self) -> str:
        return "single"
