"""An in-memory graph and vector store for the keep-old-copy swap: conditional writes follow the stores' own semantics."""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.modules.transformers.swap_commit import SwapCommitter
from app.services.graph_db.common.record_visibility import holds_vrid
from app.services.graph_db.conditions import holds
from app.services.vector_db.models import ScrollResult

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeGraph:
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.mapping: dict[str, dict[str, Any]] = {}

    async def get_document(self, key: str, collection: str, **_: object) -> dict[str, Any] | None:
        if collection != "records":
            return self.mapping.get(key)
        doc = self.records.get(key)
        return dict(doc) if doc else None

    async def get_records_by_virtual_record_id(self, vrid: str, *_: object, **__: object) -> list[str]:
        return [k for k, d in self.records.items() if d.get("isDeleted") is not True and holds_vrid(d, vrid)]

    async def update_node(self, key: str, collection: str, fields: dict[str, Any], *_: object) -> bool:
        target = self.records if collection == "records" else self.mapping
        target.setdefault(key, {}).update(fields)
        return True

    async def update_node_fields_if(self, key, collection, updates, conditions, transaction=None, *, unset=()) -> bool:
        doc = self.records.get(key)
        if doc is None or not all(holds(c, doc) for c in conditions):
            return False
        doc.update(updates)
        for name in unset:
            doc.pop(name, None)
        return True

    async def update_node_fields_if_match(self, key, collection, updates, expected, transaction=None, *, clock_at_most=None, unset=()) -> bool:
        doc = self.records.get(key)
        if doc is None or any(doc.get(f) != v for f, v in expected.items()):
            return False
        doc.update(updates)
        for name in unset:
            doc.pop(name, None)
        return True


def committer(
    graph: FakeGraph, *, collections: dict[str, str] | None = None, points: int = 3, view: Callable[[str], list[str]] | None = None,
) -> tuple[SwapCommitter, SimpleNamespace]:
    """A committer over the fake graph. ``collections`` maps a connector id to its write collection; ``view`` gives the block ids the
    vector store holds for a virtual record (by default one block, unless ``points`` is 0)."""
    collections = collections or {}
    view = view or (lambda vrid: ["b1"] if points > 0 else [])
    registry = MagicMock()
    registry.resolve_write_collection.side_effect = lambda ctx: collections.get(getattr(ctx, "connector_id", None), "c1")

    async def scroll(collection_name: str, scroll_filter: dict, limit: int, offset: str | None = None, with_payload: list[str] | None = None) -> ScrollResult:
        blocks = view(scroll_filter["virtualRecordId"])
        return ScrollResult(points=[SimpleNamespace(payload={"metadata": {"blockId": b, "virtualRecordId": scroll_filter["virtualRecordId"]}}) for b in blocks])

    async def count_points(collection_name: str, count_filter: dict) -> int:
        return len(view(count_filter["virtualRecordId"]))

    vs = SimpleNamespace(
        collection_registry=registry,
        _record_context=lambda org, record: SimpleNamespace(connector_id=getattr(record, "connector_id", None)),
        vector_db_service=SimpleNamespace(
            filter_collection=AsyncMock(side_effect=lambda must=None, **_: must), scroll=scroll, count_points=count_points,
        ),
        collection_locator=MagicMock(),
        _resync_membership_after_write=AsyncMock(),
        purge_record_vectors=AsyncMock(),
    )
    orchestrator = SimpleNamespace(graph_provider=graph, vector_store=vs, blob_storage=MagicMock())
    swap = SwapCommitter(orchestrator)  # type: ignore[arg-type]
    swap._release_unheld = AsyncMock()  # type: ignore[method-assign]
    return swap, vs


def ctx_for(record_id: str, vrid: str, pointed: list[str] | None = None, connector_id: str = "k1") -> SimpleNamespace:
    record = SimpleNamespace(id=record_id, virtual_record_id=vrid, connector_id=connector_id, connector_name="X", external_record_group_id=None)
    rc = SimpleNamespace(new_metadata={"pointedBlocks": pointed if pointed is not None else ["b1"]})
    return SimpleNamespace(record=record, reconciliation_context=rc)
