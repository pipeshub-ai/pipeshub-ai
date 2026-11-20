"""A recording fake around ``unindex_record``: which steps ran, in which order, with a step that can fail once.

Shared by the unit tests of ``unindex_record`` and by the refusal-in-lease scenario.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.indexing.unindex import unindex_record

RECORD = {"_key": "rec-1", "orgId": "org-1", "virtualRecordId": "v-1"}
LINKS = [
    {"recordId": "rec-1", "collection": "categories", "entityId": "cat-1"},
    {"recordId": "rec-1", "collection": "topics", "entityId": "top-1"},
]


class Rig:
    def __init__(self, *, holders: list[str] | None = None, links: list | None = None) -> None:
        self.calls: list[str] = []
        graph = MagicMock()
        self.graph = graph

        def note(name: str, result: object = None) -> AsyncMock:
            async def run(*_a: object, **_kw: object) -> object:
                self.calls.append(name)
                if self.fail_at == name:
                    self.fail_at = None
                    raise RuntimeError(f"{name} failed")
                return result

            return AsyncMock(side_effect=run)

        self.fail_at: str | None = None
        # Like the real graph: clearing the classification removes the record's links, so a retry reads none.
        self.links = list(LINKS if links is None else links)
        self.saved: dict[str, object] = {}
        links_read = note("links")

        async def read_links(*_a: object, **_kw: object) -> list:
            await links_read()
            return list(self.links)

        graph.get_record_taxonomy_links = AsyncMock(side_effect=read_links)
        fields = note("fields", True)
        saved = note("refs_saved", True)
        cleared = note("refs_cleared", True)

        async def update_node(_key: str, _collection: str, values: dict) -> bool:
            if "pendingTaxonomyRefs" in values:
                refs = values["pendingTaxonomyRefs"]
                done = await (cleared() if refs is None else saved())
                self.saved["pendingTaxonomyRefs"] = refs
                return done
            return await fields()

        graph.update_node = AsyncMock(side_effect=update_node)
        graph.get_records_by_virtual_record_id = note("holders", ["rec-1"] if holders is None else holders)
        graph.stage_states_delete_for_virtual_records = note("stage_states", 1)
        self.processor = MagicMock()
        self.processor.graph_provider = graph
        self.processor._release_skipped_content = note("release")
        self.store = MagicMock()
        self.store.delete_entity = note("title_deleted")
        self.processor.sink_orchestrator.entity_vector_store = self.store
        clear = note("classification")

        async def clear_classification(*_a: object, **_kw: object) -> None:
            await clear()
            self.links = []

        self.clear_classification = AsyncMock(side_effect=clear_classification)
        self.clear_for_record = note("mentions")
        self.points = note("taxonomy_points")

    async def run(self, record: dict | None = None) -> None:
        # Each attempt is a new event: it reads the record as the earlier attempt left it.
        record = dict(record or RECORD)
        if self.saved.get("pendingTaxonomyRefs"):
            record["pendingTaxonomyRefs"] = self.saved["pendingTaxonomyRefs"]
        with (
            patch("app.modules.indexing.unindex.GraphDBTransformer") as transformer,
            patch("app.modules.indexing.unindex.NamedEntityGraphWriter") as writer,
            patch("app.modules.indexing.unindex.GraphDataStore"),
            patch("app.modules.indexing.unindex.delete_unreached_taxonomy_points", self.points),
        ):
            transformer.return_value.clear_classification = self.clear_classification
            writer.return_value.clear_for_record = self.clear_for_record
            await unindex_record(record, "org-1", processor=self.processor, logger=MagicMock())
