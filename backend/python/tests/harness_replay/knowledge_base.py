"""A seeded knowledge base behind every knowledge tool the agent route grants.

When an agent has knowledge attached, the product gives it the
``knowledgegraph`` toolset (search, navigate, list_files, lookup_record) and
grants ``knowledgegraph__fetch_record`` once a tool has reported a Record ID.
In a driven run there is no graph or blob store, so each tool's storage seam
is replaced with the fixture: the vector search, the graph navigator, the
record resolver, the knowledge-hub listing service, the connector catalog and
the full-record resolver. Everything above those seams (argument handling,
rendering, ``refN`` numbering, citations, the fetch grant) is the product's.

The fixture is one knowledge-base collection; each doc is one record with one
block, so a search hit covers its record and a fetch returns all of it. With
``snippets=True`` each sentence is a block and a search returns, per record,
only the blocks that share words with the query (as production's vector search
returns a few chunks of a record), so a fact can be in the whole record and not
in what search showed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from tests.contracts.golden_support import FRONTEND, KNOWLEDGE_SOURCE_ID, ORG_ID
from tests.harness_replay.world import Doc, rank_knowledge, words
from tests.support.knowledge_builder import builder_patches

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def fixture_knowledge(searched: bool) -> tuple[dict[str, str], ...]:
    """The agent's knowledge entry for the fixture source, as the agent document stores it (both drivers)."""
    return ({"connectorId": KNOWLEDGE_SOURCE_ID, "type": "KB"},) if searched else ()
KNOWLEDGE_SOURCE_NAME = "Company knowledge base"
RECORD_PREFIX = "rec-"
EPOCH_MS = 1_759_276_800_000  # 2025-10-01, fixed so listings are stable
# The fixture's "now", as a client sends it: every dated fact in the fixture is on or before it and every scheduled one
# (renewals, price changes, the H1 2026 roadmap) after it. A run shown the wall clock instead calls a December 2025
# renewal "already passed", which the judge, holding only the fixture, fails (DUR1).
CLIENT_TIMEZONE = "UTC"
CLIENT_TIME = "2025-10-01T09:00:00Z"

_KG = "app.agents.actions.knowledge_graph"

# Every tool the product can put in front of the model for attached knowledge,
# and the seam that backs it here. A tool missing from this map is unbacked:
# ``test_wave1_offline_lane`` fails when the loop is offered one.
BACKED_TOOLS: dict[str, str] = {
    "knowledgegraph__search": "retrieval search + flatten",
    # Main's #4296/#4349 (TIMELINE_SUMMARIES, on by default): the same retrieval search, once per period.
    "knowledgegraph__search_timeline": "retrieval search per period + flatten",
    # G-58: the same retrieval search, read by the run's quarantined summarizer on the run's own model.
    "knowledgegraph__summarize": "retrieval search + flatten, then the run model",
    "knowledgegraph__navigate": "GraphNavigator",
    "knowledgegraph__list_files": "KnowledgeHubService.get_nodes",
    "knowledgegraph__lookup_record": "RecordResolver.resolve_many",
    "knowledgegraph__fetch_record": "fetch_full_record._RecordResolver",
    # Main's #3975: with Labs ENABLE_NAMED_ENTITY_EXTRACTION off (the default) it answers that named-entity
    # lookup is off and never reaches the graph.
    "knowledgegraph__find_records_by_value": "is_named_entity_extraction_enabled (off)",
    # Main's people listing: the fixture graph's entity access context and user lookup (no colleague named).
    "knowledgegraph__find_records_by_person": "IGraphDBProvider.get_entity_access_context + find_users",
}


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Blocks a snippet search shows per record: production returns a few chunks of each hit.
SNIPPET_BLOCKS = 2


def record_id(doc: Doc) -> str:
    return f"{RECORD_PREFIX}{doc.id}"


def sentences(doc: Doc) -> list[str]:
    return [s for s in _SENTENCE_END.split(doc.text.strip()) if s]


def flat_block(doc: Doc, index: int = 0, content: str | None = None, *, connector: str = "KB") -> dict[str, Any]:
    return {
        "virtual_record_id": doc.id,
        "block_index": index,
        "content": doc.text if content is None else content,
        "block_type": "text",
        "metadata": {
            "virtualRecordId": doc.id,
            "recordId": record_id(doc),
            "recordName": doc.title,
            "orgId": ORG_ID,
            "mimeType": "text/markdown",
            "origin": "UPLOAD" if connector == "KB" else "CONNECTOR",
            "connector": connector,
            "recordType": "FILE",
            "blockIndex": index,
        },
        "score": 0.9,
    }


def full_record(doc: Doc, *, snippets: bool = False, connector: str = "KB") -> dict[str, Any]:
    """The record as the blob store returns it: one text block, or one per sentence with ``snippets``
    (and, as production's record header does, its Record ID)."""
    rid = record_id(doc)
    texts = sentences(doc) if snippets else [doc.text]
    header = f"Record ID: {rid}\nRecord: {doc.title}" if snippets else f"Record: {doc.title}"
    return {
        "virtual_record_id": doc.id, "id": rid, "_key": rid, "record_name": doc.title,
        "frontend_url": FRONTEND, "context_metadata": header,
        "block_containers": {
            "blocks": [{"index": i, "type": "text", "data": t} for i, t in enumerate(texts)], "block_groups": [],
        },
        "origin": "UPLOAD" if connector == "KB" else "CONNECTOR",
        "connector_name": "KNOWLEDGE_BASE" if connector == "KB" else connector, "mime_type": "text/plain",
        "record_type": "FILE", "version": 1,
    }


@dataclass(frozen=True)
class FixtureKnowledgeBase:
    docs: tuple[Doc, ...]
    snippets: bool = False
    # Doc ids whose content a search or a fetch rendered to the request (any agent of it): what the run read.
    shown: set[str] = field(default_factory=set[str])
    # A seeded tenant's records (G-49): doc id -> the connector it came from (``KB`` when absent), and which docs
    # the run's actor may find, as the vector search filters by the graph's accessible records. Reads of a doc are
    # decided by the product's own access check against the graph.
    connectors: Mapping[str, str] = field(default_factory=dict[str, str])
    searchable: Callable[[str], bool] | None = None

    def connector(self, doc: Doc) -> str:
        return self.connectors.get(doc.id, "KB")

    def by_record_id(self, rid: str) -> Doc | None:
        return next((d for d in self.docs if record_id(d) == rid or d.id == rid), None)

    def resolve_identifier(self, ident: str) -> Doc | None:
        """A record id, a doc id, a URL naming one, or a key in a title (``NW-112``)."""
        ident = ident.strip()
        direct = self.by_record_id(ident)
        if direct is not None:
            return direct
        for doc in self.docs:
            if record_id(doc) in ident:
                return doc
        lowered = ident.lower()
        return next((d for d in self.docs if lowered and lowered in d.title.lower()), None)

    def patches(self, retrieval: Any) -> list[tuple[str, object]]:  # noqa: ANN401 - the route's AsyncMock
        retrieval.search_with_filters.side_effect = self._search
        patched = builder_patches(f"{_KG}.ops.search.KnowledgeContextBuilder", self._flatten)
        return [
            *patched,
            # search_timeline (main #4296) imports the builder when it renders, from its own module.
            ("app.modules.retrieval.context.builder.KnowledgeContextBuilder", patched[0][1]),
            (f"{_KG}.knowledge_graph.ConnectorCatalog.build", self._catalog),
            (f"{_KG}.knowledge_graph.GraphNavigator", self._navigator_class()),
            (f"{_KG}.knowledge_graph.RecordResolver", self._resolver_class()),
            (f"{_KG}.ops.listing.KnowledgeHubService", self._hub_class()),
            ("app.utils.fetch_full_record._RecordResolver", self._fetch_resolver_class()),
        ]

    async def _search(self, *, queries: list[str], **_: object) -> dict[str, Any]:
        query = " ".join(queries)
        found = [d for d in self.docs if self.searchable is None or self.searchable(d.id)]
        hits = rank_knowledge(found, query)
        results = [
            {"metadata": {"virtualRecordId": doc.id, "blockIndex": index}}
            for doc in hits
            for index in self._shown_blocks(doc, query)
        ]
        return {"status_code": 200, "searchResults": results, "virtual_to_record_map": {}}

    def _shown_blocks(self, doc: Doc, query: str) -> list[int]:
        """The whole record (block 0) by default; with ``snippets``, the sentences sharing most words with the
        query (the first sentence when only the title matched)."""
        if not self.snippets:
            return [0]
        wanted = words(query)
        scored = [(len(wanted & words(text)), i) for i, text in enumerate(sentences(doc))]
        matching = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))[:SNIPPET_BLOCKS]
        return sorted(i for _, i in matching) or [0]

    async def _flatten(
        self,
        search_results: list[dict[str, Any]],
        blob_store: object,
        org_id: str,
        is_multimodal_llm: bool,
        virtual_record_id_to_result: dict[str, Any],
        virtual_to_record_map: dict[str, Any],
        **_: object,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for result in search_results:
            doc = self.by_record_id(result["metadata"]["virtualRecordId"])
            if doc is None:
                continue
            connector = self.connector(doc)
            virtual_record_id_to_result[doc.id] = full_record(doc, snippets=self.snippets, connector=connector)
            self.shown.add(doc.id)
            if self.snippets:
                index = int(result["metadata"].get("blockIndex") or 0)
                out.append(flat_block(doc, index, sentences(doc)[index], connector=connector))
            else:
                out.append(flat_block(doc, connector=connector))
        return out

    async def _catalog(self, *_: object, **__: object) -> object:
        from app.agents.actions.knowledge_graph.catalog import (
            ConnectorCatalog,
            ConnectorInfo,
        )

        return ConnectorCatalog(
            [ConnectorInfo(id=KNOWLEDGE_SOURCE_ID, name=KNOWLEDGE_SOURCE_NAME, type="KB")]
        )

    def _navigator_class(self) -> type:
        kb = self

        class FixtureNavigator:
            def __init__(self, **_: object) -> None:
                pass

            async def navigate(
                self, *, node_id: str | None = None, page: int = 1, limit: int = 50, **_: object
            ) -> object:
                return kb.navigation_view(node_id, page, limit)

        return FixtureNavigator

    def navigation_view(self, node_id: str | None, page: int, limit: int) -> object:
        from app.agents.actions.knowledge_graph.models import (
            NavigationView,
            NodeRef,
            NodeRow,
            PaginationInfo,
        )

        collection = NodeRef(
            id=KNOWLEDGE_SOURCE_ID, name=KNOWLEDGE_SOURCE_NAME, node_type="recordGroup",
            sub_type="COLLECTION", is_record=False,
        )
        if not node_id or node_id == "root":
            rows = [
                NodeRow(
                    **collection.model_dump(), has_children=True, detail=f"{len(self.docs)} records"
                )
            ]
            return NavigationView(
                current=None, breadcrumbs=[], rows=rows, related=[], pagination=None,
                web_url=None, indexing_status=None, connector=None,
            )
        if node_id == KNOWLEDGE_SOURCE_ID:
            start = (page - 1) * limit
            docs = self.docs[start : start + limit]
            rows = [
                NodeRow(
                    id=record_id(d), name=d.title, node_type="record", sub_type="FILE",
                    is_record=True, has_children=False, detail=None, indexing_status="COMPLETED",
                )
                for d in docs
            ]
            pagination = PaginationInfo(
                page=page, limit=limit, total=len(self.docs),
                has_next=start + limit < len(self.docs), has_prev=page > 1,
            )
            return NavigationView(
                current=collection, breadcrumbs=[], rows=rows, related=[], pagination=pagination,
                web_url=None, indexing_status=None, connector="KB",
            )
        doc = self.resolve_identifier(node_id)
        if doc is None:
            return NavigationView(
                current=None, breadcrumbs=[], rows=[], related=[], pagination=None,
                web_url=None, indexing_status=None, connector=None,
            )
        current = NodeRef(
            id=record_id(doc), name=doc.title, node_type="record", sub_type="FILE", is_record=True
        )
        return NavigationView(
            current=current, breadcrumbs=[collection], rows=[], related=[], pagination=None,
            web_url=None, indexing_status="COMPLETED", connector="KB",
            context_block=f"Record ID: {record_id(doc)}\nRecord: {doc.title}\nType: FILE",
        )

    def _resolver_class(self) -> type:
        kb = self

        class FixtureResolver:
            def __init__(self, **_: object) -> None:
                pass

            async def resolve_many(self, identifiers: list[str]) -> object:
                return kb.lookup(identifiers)

        return FixtureResolver

    def lookup(self, identifiers: list[str]) -> object:
        from app.agents.actions.knowledge_graph.models import LookupMatch, LookupResult

        matches: list[LookupMatch] = []
        missing: list[str] = []
        for ident in identifiers:
            doc = self.resolve_identifier(ident)
            if doc is None:
                missing.append(ident)
                continue
            matches.append(
                LookupMatch(
                    id=record_id(doc), name=doc.title, record_type="FILE",
                    connector_name=KNOWLEDGE_SOURCE_NAME, web_url=None,
                    indexing_status="COMPLETED", identifier_used=ident,
                )
            )
        return LookupResult(matches=matches, ambiguous=False, not_found_identifiers=missing)

    def _hub_class(self) -> type:
        kb = self

        class FixtureKnowledgeHub:
            def __init__(self, **_: object) -> None:
                pass

            async def get_nodes(
                self, *, q: str | None = None, page: int = 1, limit: int = 20, **_: object
            ) -> object:
                return kb.list_nodes(q, page, limit)

        return FixtureKnowledgeHub

    def list_nodes(self, query: str | None, page: int, limit: int) -> object:
        from app.connectors.sources.localKB.api.knowledge_hub_models import (
            KnowledgeHubNodesResponse,
            NodeItem,
        )

        if query:
            wanted = words(query)
            docs = [d for d in self.docs if wanted & words(d.title)]
        else:
            docs = list(self.docs)
        start = (page - 1) * limit
        items = [
            NodeItem.model_validate(
                {
                    "id": record_id(d), "name": d.title, "nodeType": "record",
                    "parentId": KNOWLEDGE_SOURCE_ID, "origin": "COLLECTION", "recordType": "FILE",
                    "indexingStatus": "COMPLETED", "createdAt": EPOCH_MS, "updatedAt": EPOCH_MS,
                    "hasChildren": False, "mimeType": "text/markdown",
                }
            )
            for d in docs[start : start + limit]
        ]
        return KnowledgeHubNodesResponse.model_validate({"success": True, "items": items})

    def _fetch_resolver_class(self) -> type:
        from app.utils.fetch_full_record import (
            UNAVAILABLE,
            _RecordResolver,  # pyright: ignore[reportPrivateUsage]
        )

        kb = self

        class FixtureRecordResolver(_RecordResolver):
            """A record the map does not hold yet is loaded from the fixture,
            the way the product loads it from the graph and blob store. An id the
            fixture does not hold is one the graph has no record for: "not available",
            as in the product, never the drivers' mocked graph answering it with a
            document that is "still being indexed" (DUR1)."""

            @override
            async def resolve(self, record_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
                if self._from_map(record_id) is None:  # pyright: ignore[reportPrivateUsage]
                    doc = kb.by_record_id(record_id)
                    if doc is None:
                        return record_id, None, UNAVAILABLE
                    self._map[doc.id] = full_record(  # pyright: ignore[reportPrivateUsage]
                        doc, snippets=kb.snippets, connector=kb.connector(doc)
                    )
                resolved = await super().resolve(record_id)
                if resolved[1] is not None and (doc := kb.by_record_id(record_id)) is not None:
                    kb.shown.add(doc.id)
                return resolved

        return FixtureRecordResolver


__all__ = [
    "BACKED_TOOLS",
    "CLIENT_TIME",
    "CLIENT_TIMEZONE",
    "KNOWLEDGE_SOURCE_ID",
    "FixtureKnowledgeBase",
    "flat_block",
    "full_record",
    "record_id",
    "sentences",
]
