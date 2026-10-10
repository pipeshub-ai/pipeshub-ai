"""Record labels against a real Neo4j 5.26 and a real ArangoDB 3.12.

The new reads are Cypher and AQL strings, so only a server can show they
parse and return what the unit fakes assume:

- ``get_record_taxonomy_links`` returns each edge with its own spelling;
- a record's details name its taxonomy items by its own spelling;
- ``copy_document_relationships`` carries the spelling onto the copy;
- end to end, with the real resolver and graph transformer writing two
  records that share a topic and a category, a user who can open only one of
  them sees only that record's spellings in its details and in
  search_entities, in both indexing orders, and the name filter built from
  search_entities still finds the record;
- the label repair restores a stored copy from the edges.

Needs the graph services, and skips cleanly when they are not reachable:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it        # or arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_label_scope_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.search import resolve_entity_filter_groups
from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import ResolutionMode
from app.modules.entity_resolution.resolver import EntityResolver
from app.modules.indexing.record_label_repair import (
    REPAIR_VERSION,
    RecordLabelRepair,
    RecordLabelRepairState,
)
from app.modules.transformers.graphdb import GraphDBTransformer
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.modules.entity_resolution.conftest import (
    FakeEntityVectorStore,
    ScriptedModel,
    _tokens,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "record_label_scope_it"

RECORDS = CollectionNames.RECORDS.value
TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
BELONGS_TO_CATEGORY = CollectionNames.BELONGS_TO_CATEGORY.value
OTHER_RECORD_WORDS = ("falcon", "codename")

logger = logging.getLogger("record-label-scope-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    backend: str
    suffix: str
    org_id: str
    user_key: str
    user_id: str
    connector_id: str
    records: list[str] = field(default_factory=list)


async def _connect(backend: str, monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    if backend == "neo4j":
        monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider: IGraphDBProvider = Neo4jProvider(logger, MagicMock())
    else:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
        })
        provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    return provider


async def _remove_test_data(env: _Env) -> None:
    graph = env.graph
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.orgId = $o OR n.itOrg = $o OR n.id IN $ids OR n.connectorId = $c DETACH DELETE n",
            parameters={
                "o": env.org_id, "c": env.connector_id,
                "ids": [*env.records, env.user_key, env.org_id, env.connector_id],
            },
        )
        return
    for edges in (
        CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
        CollectionNames.USER_APP_RELATION.value, BELONGS_TO_TOPIC, BELONGS_TO_CATEGORY,
        CollectionNames.INTER_CATEGORY_RELATIONS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @s) OR CONTAINS(e._to, @s) REMOVE e IN {edges}",
            {"s": env.suffix},
        )
    for collection in (TOPICS, CATEGORIES, RECORDS, CollectionNames.APPS.value, CollectionNames.USERS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.orgId == @o OR CONTAINS(d._key, @s) REMOVE d IN {collection}",
            {"o": env.org_id, "s": env.suffix},
        )
    await graph.http_client.execute_aql(
        f"FOR d IN {CollectionNames.ORGS.value} FILTER d._key == @o REMOVE d IN {CollectionNames.ORGS.value}",
        {"o": env.org_id},
    )


def _edge(frm: str, frm_col: str, to: str, to_col: str, **extra: object) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {
        "from_id": frm, "from_collection": frm_col, "to_id": to, "to_collection": to_col,
        "createdAtTimestamp": now, **extra,
    }


async def _add_record(env: _Env, label: str, *, shared: bool, extracted_at: int = 1) -> str:
    now = get_epoch_timestamp_in_ms()
    record_id = f"rec-{label}-{env.suffix}"
    await env.graph.batch_upsert_nodes(
        [{
            "id": record_id, "orgId": env.org_id, "recordName": f"{label} note",
            "externalRecordId": f"ext-{record_id}", "recordType": "FILE", "origin": "CONNECTOR",
            "connectorName": "WEB", "connectorId": env.connector_id, "version": 0,
            "virtualRecordId": f"vr-{record_id}", "indexingStatus": ProgressStatus.COMPLETED.value,
            "lastExtractionTimestamp": extracted_at,
            "isDeleted": False, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=RECORDS,
    )
    env.records.append(record_id)
    if shared:
        await env.graph.batch_create_edges(
            [_edge(env.org_id, CollectionNames.ORGS.value, record_id, RECORDS, type="ORG", role="READER",
                   updatedAtTimestamp=now)],
            collection=CollectionNames.PERMISSION.value,
        )
    return record_id


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await _connect(request.param, monkeypatch)
        except Exception as exc:
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        assert await graph.ensure_schema() is not False, f"ensure_schema failed on {request.param}"

        suffix = uuid.uuid4().hex[:10]
        environment = _Env(
            graph=graph, backend=request.param, suffix=suffix, org_id=f"org-rls-{suffix}",
            user_key=f"user-rls-{suffix}", user_id=f"uid-user-rls-{suffix}",
            connector_id=f"web-rls-{suffix}",
        )
        cleanup.push_async_callback(_remove_test_data, environment)
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{"id": environment.org_id, "name": "Org", "accountType": "enterprise", "isActive": True}],
            collection=CollectionNames.ORGS.value,
        )
        await graph.batch_upsert_nodes(
            [{
                "id": environment.connector_id, "name": "Web", "type": "Web", "appGroup": "Web",
                "scope": "team", "orgId": environment.org_id, "isActive": True,
                "vectorMembershipBackfilled": True,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        await graph.batch_upsert_nodes(
            [{
                "id": environment.user_key, "userId": environment.user_id, "orgId": environment.org_id,
                "email": f"{environment.user_key}@example.com", "fullName": "Reader", "isActive": True,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.USERS.value,
        )
        await graph.batch_create_edges(
            [_edge(environment.user_key, CollectionNames.USERS.value, environment.org_id,
                   CollectionNames.ORGS.value, entityType="ORGANIZATION", updatedAtTimestamp=now)],
            collection=CollectionNames.BELONGS_TO.value,
        )
        await graph.batch_create_edges(
            [_edge(environment.user_key, CollectionNames.USERS.value, environment.connector_id,
                   CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now,
                   updatedAtTimestamp=now)],
            collection=CollectionNames.USER_APP_RELATION.value,
        )
        yield environment


async def _node(env: _Env, collection: str, key: str, name: str, *, canonical: bool = True) -> None:
    node: dict[str, Any] = {"id": key, "name": name, "createdAtTimestamp": 1}
    if canonical:
        node |= {"normalizedName": name.casefold(), "orgId": env.org_id}
    else:
        node["itOrg"] = env.org_id
    await env.graph.create_taxonomy_node_if_absent(collection, node)


async def _link(env: _Env, record: str, edges: str, collection: str, key: str, **extra: object) -> None:
    await env.graph.batch_create_edges([_edge(record, RECORDS, key, collection, **extra)], collection=edges)


def _assert_only_own_words(text: str, where: str) -> None:
    found = [w for w in OTHER_RECORD_WORDS if w in text.casefold()]
    assert not found, f"{where} shows {found}: {text}"


class TestTheNewReads:
    async def test_links_details_and_copies(self, env: _Env) -> None:
        s = env.suffix
        topic, legacy, migrated, category = f"t-{s}", f"t-legacy-{s}", f"t-mig-{s}", f"c-{s}"
        await _node(env, TOPICS, topic, "Falcon launch window")
        await _node(env, TOPICS, legacy, "Legacy topic", canonical=False)
        await _node(env, TOPICS, migrated, "Migrated topic")
        await _node(env, CATEGORIES, category, "Codename programme")
        open_record = await _add_record(env, "open", shared=True)
        hidden = await _add_record(env, "hidden", shared=False)
        await _link(env, open_record, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Product launch window")
        await _link(env, open_record, BELONGS_TO_TOPIC, TOPICS, legacy)
        await _link(env, open_record, BELONGS_TO_TOPIC, TOPICS, migrated, migratedFrom=f"{TOPICS}/{legacy}")
        await _link(env, open_record, BELONGS_TO_CATEGORY, CATEGORIES, category, extractedName="Product programme")
        await _link(env, hidden, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Falcon launch window")

        rows = await env.graph.get_record_taxonomy_links([open_record, hidden])
        got = {
            (r["recordId"], r["collection"], r["entityId"], r["name"], r["canonical"], r["extractedName"], r["migrated"])
            for r in rows
        }
        assert got == {
            (open_record, TOPICS, topic, "Falcon launch window", True, "Product launch window", False),
            (open_record, TOPICS, legacy, "Legacy topic", False, None, False),
            (open_record, TOPICS, migrated, "Migrated topic", True, None, True),
            (open_record, CATEGORIES, category, "Codename programme", True, "Product programme", False),
            (hidden, TOPICS, topic, "Falcon launch window", True, "Falcon launch window", False),
        }

        details = await env.graph.check_record_access_with_details(env.user_id, env.org_id, open_record)
        assert details is not None
        metadata = details["metadata"]
        assert sorted(t["name"] for t in metadata["topics"]) == [
            "Legacy topic", "Migrated topic", "Product launch window",
        ]
        assert [c["name"] for c in metadata["categories"]] == ["Product programme"]
        assert set(metadata["topics"][0]) == {"id", "name"}
        _assert_only_own_words(json.dumps(metadata), "record details")

        copy = await _add_record(env, "copy", shared=True)
        assert await env.graph.copy_document_relationships(open_record, copy)
        copied = {
            r["entityId"]: r["extractedName"]
            for r in await env.graph.get_record_taxonomy_links([copy])
        }
        assert copied[topic] == "Product launch window"
        assert copied[category] == "Product programme"
        assert copied[legacy] is None


class TestEdgeSpellings:
    async def test_every_spelling_on_an_edge_is_read(self, env: _Env) -> None:
        topic = f"t-nda-{env.suffix}"
        await _node(env, TOPICS, topic, "Mutual NDA")
        record = await _add_record(env, "two", shared=True)
        await _link(env, record, BELONGS_TO_TOPIC, TOPICS, topic,
                    extractedName="NDA", extractedNames=["NDA", "Non-disclosure agreement"])

        (row,) = await env.graph.get_record_taxonomy_links([record])
        assert row["extractedNames"] == ["NDA", "Non-disclosure agreement"]
        details = await env.graph.check_record_access_with_details(env.user_id, env.org_id, record)
        assert details is not None
        assert [t["name"] for t in details["metadata"]["topics"]] == ["NDA", "Non-disclosure agreement"]

    async def test_a_copy_keeps_the_targets_own_spelling(self, env: _Env) -> None:
        topic = f"t-copy-{env.suffix}"
        await _node(env, TOPICS, topic, "Launch plan")
        source = await _add_record(env, "source", shared=True)
        target = await _add_record(env, "target", shared=True)
        await _link(env, source, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Source spelling",
                    extractedNames=["Source spelling"])
        await _link(env, target, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Target spelling",
                    extractedNames=["Target spelling"])

        assert await env.graph.copy_document_relationships(source, target)

        (row,) = await env.graph.get_record_taxonomy_links([target])
        assert row["extractedName"] == "Target spelling"
        assert row["extractedNames"] == ["Target spelling"]

    async def test_a_copy_carries_every_spelling_onto_a_new_edge(self, env: _Env) -> None:
        topic = f"t-copy2-{env.suffix}"
        await _node(env, TOPICS, topic, "Launch plan")
        source = await _add_record(env, "source2", shared=True)
        target = await _add_record(env, "target2", shared=True)
        await _link(env, source, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Plan",
                    extractedNames=["Plan", "Launch planning"])

        assert await env.graph.copy_document_relationships(source, target)

        (row,) = await env.graph.get_record_taxonomy_links([target])
        assert (row["extractedName"], row["extractedNames"]) == ("Plan", ["Plan", "Launch planning"])


class TestAReindexRespellsAnExistingEdge:
    async def test_an_edge_without_a_spelling_gets_it_and_keeps_its_other_fields(self, env: _Env) -> None:
        key = taxonomy_node_key(env.org_id, TOPICS, "release checklist")
        await _node(env, TOPICS, key, "Release checklist")
        record = await _add_record(env, "reindexed", shared=True)
        await _link(env, record, BELONGS_TO_TOPIC, TOPICS, key, mergedFrom=f"{TOPICS}/older-{env.suffix}")
        meta = SemanticMetadata(categories=[], topics=["release checklist"], languages=[], departments=[])
        ctx = MagicMock(
            record=MagicMock(id=record, org_id=env.org_id, virtual_record_id=f"vr-{record}",
                             semantic_metadata=meta, is_vlm_ocr_processed=False),
            entity_resolution=None, settings={},
        )
        resolver = EntityResolver(
            logger=logger, config_service=MagicMock(), graph_provider=env.graph,
            entity_vector_store=FakeEntityVectorStore(), mode=ResolutionMode.APPLY,
        )

        await resolver.resolve(ctx)
        await GraphDBTransformer(graph_provider=env.graph, logger=logger).apply(ctx)

        (row,) = await env.graph.get_record_taxonomy_links([record])
        assert (row["entityId"], row["extractedName"], row["extractedNames"]) == (
            key, "release checklist", ["release checklist"],
        )
        (edge,) = await env.graph.get_edges_from_node_with_target_name(f"{RECORDS}/{record}", BELONGS_TO_TOPIC)
        assert edge["mergedFrom"] == f"{TOPICS}/older-{env.suffix}"
        assert edge["createdAtTimestamp"]


class _SearchableStore(FakeEntityVectorStore):
    async def search_entities_passes(
        self, query, org_id, passes, *, entity_types=None, top_k=10, **_kw,
    ) -> list[list[dict[str, Any]]]:
        hits = [
            {**point, "score": 0.9}
            for (point_org, entity_type, _k), point in sorted(self.points.items())
            if point_org == org_id and (not entity_types or entity_type in entity_types)
            and _tokens(query) & _tokens(point["name"])
        ]
        return [hits[:top_k] for _ in passes]


SCRIPTS = {
    "unread_first": {
        "product programme": ("same", "Codename Falcon programme"),
        "product launch window": ("same", "Falcon launch window"),
    },
    "open_first": {
        "codename falcon programme": ("same", "Product programme"),
        "falcon launch window": ("same", "Product launch window"),
    },
}


@pytest.mark.parametrize("order", ["unread_first", "open_first"])
class TestLabelsShownToAUserComeOnlyFromRecordsTheyCanOpen:
    async def test_details_and_search_entities(
        self, env: _Env, order: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A freshly started server plans its first queries slowly; this test
        # is about names, not the search deadline.
        monkeypatch.setattr("app.modules.retrieval.entity_permissions.SEARCH_DEADLINE_SECONDS", 60.0)
        store = _SearchableStore()
        model = ScriptedModel(SCRIPTS[order])

        async def _invoke(llm, messages, schema, max_retries=2, **kwargs) -> Any:  # noqa: ANN401
            return await model(llm, messages, schema, max_retries, **kwargs)

        open_record = await _add_record(env, "open", shared=True)
        unread = await _add_record(env, "unread", shared=False)
        metadata = {
            unread: lambda: SemanticMetadata(
                categories=["Codename Falcon programme"], topics=["Falcon launch window"],
                languages=[], departments=[], summary="Planning brief.",
            ),
            open_record: lambda: SemanticMetadata(
                categories=["Product programme"], topics=["Product launch window"],
                languages=[], departments=[], summary="Shared launch plan.",
            ),
        }
        resolver = EntityResolver(
            logger=logger, config_service=MagicMock(), graph_provider=env.graph,
            entity_vector_store=store, mode=ResolutionMode.APPLY,
        )
        transformer = GraphDBTransformer(graph_provider=env.graph, logger=logger)
        first, second = (unread, open_record) if order == "unread_first" else (open_record, unread)
        shown_meta = {}
        with patch(
            "app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
            side_effect=_invoke,
        ), patch(
            "app.modules.entity_resolution.resolver.get_llm_for_role",
            new=AsyncMock(return_value=(MagicMock(), {})),
        ):
            for record_id in (first, second):
                meta = metadata[record_id]()
                record = MagicMock(id=record_id, org_id=env.org_id, virtual_record_id=f"vr-{record_id}",
                                   semantic_metadata=meta, is_vlm_ocr_processed=False)
                ctx = MagicMock(record=record, entity_resolution=None, settings={})
                await resolver.resolve(ctx)
                touched = await transformer.apply(ctx)
                for entity in touched:
                    entity.org_id = env.org_id
                await store.upsert_entities_batch(touched)
                shown_meta[record_id] = meta

        assert shown_meta[open_record].topics == ["Product launch window"]
        _assert_only_own_words(json.dumps(shown_meta[open_record].model_dump()), "record metadata")

        details = await env.graph.check_record_access_with_details(env.user_id, env.org_id, open_record)
        assert details is not None
        assert [t["name"] for t in details["metadata"]["topics"]] == ["Product launch window"]
        _assert_only_own_words(json.dumps(details["metadata"]), "record details")

        state = {
            "org_id": env.org_id, "user_id": env.user_id, "graph_provider": env.graph,
            "entity_vector_store": store, "apps": [env.connector_id],
        }
        ok, text = await execute_search_entities(state, "launch programme")
        assert ok, text
        shown = {r["entityType"]: r["name"] for r in json.loads(text)["results"]}
        assert shown == {"topic": "Product launch window", "category": "Product programme"}
        _assert_only_own_words(text, "search_entities")

        topic_id = next(r["entityId"] for r in json.loads(text)["results"] if r["entityType"] == "topic")
        filters = resolve_entity_filter_groups(state, [topic_id])
        found = await env.graph._get_virtual_ids_for_connector(
            env.user_id, env.org_id, env.connector_id, metadata_filters=filters,
        )
        assert set(found.values()) == {open_record}


class _Blob:
    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.writes: list[str] = []

    async def get_document_id_by_virtual_record_id(self, vrid) -> dict[str, str] | None:
        return {"record_doc_id": f"doc-{vrid}"} if vrid in self.stored else None

    async def get_record_from_storage(self, vrid, org_id, lookup_result=None) -> dict[str, Any] | None:
        return json.loads(json.dumps(self.stored[vrid])) if vrid in self.stored else None

    async def update_record_buffer(self, org_id, document_id, record_dict, vrid) -> tuple[str, int]:
        self.writes.append(vrid)
        self.stored[vrid] = record_dict
        return document_id, 1


class _Leader:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True


class TestTheRepairOnARealGraph:
    async def test_a_rewritten_copy_gets_its_own_labels_back(self, env: _Env) -> None:
        s = env.suffix
        topic, category = f"t-{s}", f"c-{s}"
        await _node(env, TOPICS, topic, "Falcon launch window")
        await _node(env, CATEGORIES, category, "Codename programme")
        open_record = await _add_record(env, "open", shared=True)
        copy = await _add_record(env, "copy", shared=True)
        await _link(env, open_record, BELONGS_TO_TOPIC, TOPICS, topic, extractedName="Product launch window")
        await _link(env, open_record, BELONGS_TO_CATEGORY, CATEGORIES, category, extractedName="Product programme")
        await _link(env, copy, BELONGS_TO_TOPIC, TOPICS, topic)
        blob = _Blob()
        for record_id in (open_record, copy):
            blob.stored[f"vr-{record_id}"] = {
                "id": record_id, "record_name": record_id,
                "semantic_metadata": {
                    "summary": "s", "categories": ["Codename programme"], "topics": ["Falcon launch window"],
                },
            }

        repair = RecordLabelRepair(
            logger=logger, graph_provider=env.graph, blob_store=blob, lock=_Leader(),
            cutoff_ms=get_epoch_timestamp_in_ms(), page_size=1,
        )
        for _ in range(20):
            await repair.tick()
            app = await env.graph.get_document(env.connector_id, CollectionNames.APPS.value)
            if app.get(RecordLabelRepairState.STATE) == REPAIR_VERSION:
                break

        assert app[RecordLabelRepairState.STATE] == REPAIR_VERSION
        assert app[RecordLabelRepairState.REPAIRED] == 1
        assert app[RecordLabelRepairState.SKIPPED] == 1
        assert blob.stored[f"vr-{open_record}"]["semantic_metadata"] == {
            "summary": "s", "categories": ["Product programme"], "topics": ["Product launch window"],
            "own_labels": True,
        }
        assert blob.writes == [f"vr-{open_record}"]
