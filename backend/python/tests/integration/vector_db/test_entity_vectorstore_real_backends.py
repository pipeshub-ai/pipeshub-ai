"""``EntityVectorStore`` against real vector backends.

The unit tests mock ``IVectorDBService``; these run the store's own code on
each backend the deployment can be installed with, and check what only the
server can show:

- Membership merges read the point back by id, so a second writer sees the
  first writer's connector even before the index refreshes (OpenSearch's
  30s ``refresh_interval``).
- Metadata read back from Redis keeps its string types: a level-``"1"``
  subcategory still matches, and an unchanged point is not re-embedded.
- A membership-only change is written with ``update_payload_by_ids`` and
  leaves the point's text, metadata and vector in place; an id with no point
  is ignored.
- Connector cleanup strips shared entities, deletes exclusive ones and the
  connector's record and record-group points, pages by re-reading from the
  start, and leaves other orgs alone; a point the graph still links is
  rewritten rather than deleted.
- Deletes never touch the embedding model.

Searches, filtered scrolls and filtered deletes are served from the last
refresh on OpenSearch, so tests publish their writes before one of those.

Embeddings are a deterministic stub (no model download). Each backend is
its own fixture, so the suite runs on whichever services are up and skips
the rest:

  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml \\
    up -d --wait redis-vector-it opensearch-vector-it
  docker run -d --name qdrant-it -p 6343:6333 qdrant/qdrant:v1.15
  cd backend/python && pytest tests/integration/vector_db/test_entity_vectorstore_real_backends.py -m integration

Environment: QDRANT_HTTP_PORT (6343), REDIS_VECTOR_HOST/PORT (6399),
OPENSEARCH_HOST/PORT (9299).
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import (
    EntityPointRef,
    EntityVectorStore,
)
from app.services.vector_db.models import HealthStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.vector_db.interface.vector_db import IVectorDBService

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

logger = logging.getLogger("entity-store-it")
DIM = 16


async def _best(store, names, org_id, entity_type, level=None) -> list:
    """The first candidate per name, as the resolver's single best match."""
    found = await store.find_candidates(names, org_id, entity_type, level, k=1)
    return [c[0] if c else None for c in found]


class _StubEmbeddings:
    """Deterministic unit vectors from the text's hash: equal text, equal vector."""

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.casefold().encode()).digest()
        raw = [b / 255.0 - 0.5 for b in digest[:DIM]]
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        return [x / norm for x in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


async def _connect(service: IVectorDBService, label: str) -> None:
    try:
        await service.connect()
        health = await service.health_check()
    except Exception as exc:
        pytest.skip(f"{label} not available: {exc}")
    if health.status == HealthStatus.UNHEALTHY:
        pytest.skip(f"{label} not available: {health.message}")


async def _qdrant() -> IVectorDBService:
    pytest.importorskip("qdrant_client")
    from app.services.vector_db.qdrant.config import QdrantConfig
    from app.services.vector_db.qdrant.qdrant import QdrantService

    # HTTP only: with gRPC the client always dials 6334, whatever `port` says.
    port = int(os.environ.get("QDRANT_HTTP_PORT", "6343"))
    service = QdrantService(QdrantConfig.from_dict({"host": "localhost", "port": port, "prefer_grpc": False}))
    await _connect(service, f"Qdrant:{port}")
    return service


async def _redis() -> IVectorDBService:
    pytest.importorskip("redis")
    from app.services.vector_db.redis.config import RedisVectorConfig
    from app.services.vector_db.redis.redis_vector import RedisVectorService

    host = os.environ.get("REDIS_VECTOR_HOST", "localhost")
    port = int(os.environ.get("REDIS_VECTOR_PORT", "6399"))
    service = RedisVectorService(RedisVectorConfig(host=host, port=port))
    await _connect(service, f"Redis:{port}")
    return service


async def _opensearch() -> IVectorDBService:
    pytest.importorskip("opensearchpy")
    from app.services.vector_db.opensearch.config import OpenSearchConfig
    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    host = os.environ.get("OPENSEARCH_HOST", "localhost")
    port = int(os.environ.get("OPENSEARCH_PORT", "9299"))
    service = OpenSearchService(OpenSearchConfig(
        host=host, port=port, username="admin", password="admin", use_ssl=False, verify_certs=False,
    ))
    await _connect(service, f"OpenSearch:{port}")
    return service


@pytest.fixture(params=["qdrant", "redis", "opensearch"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[EntityVectorStore]:
    service = await {"qdrant": _qdrant, "redis": _redis, "opensearch": _opensearch}[request.param]()
    collection = f"entities_it_{uuid.uuid4().hex[:8]}"
    store = EntityVectorStore(
        logger=logger, config_service=MagicMock(), vector_db_service=service, collection_name=collection,
    )

    async def _stub_embeddings() -> None:
        store._dense_embeddings = _StubEmbeddings()
        store._embedding_size = DIM
        store._model_id = "stub:hash"
        # No sparse embedder: nothing here downloads a model.

    store._init_embeddings = _stub_embeddings  # type: ignore[method-assign]
    store.backend = request.param  # type: ignore[attr-defined]
    try:
        await store._ensure_initialized()
        yield store
    finally:
        try:
            await service.delete_collection(collection)
        except Exception:
            logger.warning("could not drop %s", collection)
        disconnect = getattr(service, "disconnect", None)
        if disconnect is not None:
            await disconnect()


def _entity(
    entity_id: str,
    entity_type: EntityType = EntityType.TOPIC,
    *,
    org: str,
    name: str | None = None,
    connectors: list[str] | None = None,
    groups: list[str] | None = None,
    level: str | None = None,
    aliases: list[str] | None = None,
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name or f"Name {entity_id}",
        org_id=org,
        level=level,
        aliases=aliases or [],
        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
        connector_ids=connectors or [],
        record_group_ids=groups or [],
    )


async def _point(store: EntityVectorStore, org: str, entity_type: str, entity_id: str) -> dict[str, Any] | None:
    point_id = store._point_id(org, entity_type, entity_id)
    points = await store.vector_db_service.retrieve_points(store.collection_name, [point_id])
    return points[0].payload if points else None


async def _publish_writes(store: EntityVectorStore) -> None:
    if getattr(store, "backend", None) == "opensearch":
        await store.vector_db_service.client.indices.refresh(index=store.collection_name)  # type: ignore[attr-defined]


class TestMembershipReads:
    async def test_a_second_writer_sees_the_first_writers_membership(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("eng", EntityType.DEPARTMENT, org=org, connectors=["drive"], groups=["g1"])])
        await store.upsert_entities_batch([_entity("eng", EntityType.DEPARTMENT, org=org, connectors=["jira"], groups=["g2"])])

        payload = await _point(store, org, "department", "eng")
        assert payload is not None
        assert payload["connectorIds"] == ["drive", "jira"]
        assert payload["recordGroupIds"] == ["g1", "g2"]

    async def test_unchanged_point_is_not_rewritten(self, store: EntityVectorStore) -> None:
        """Redis hands metadata back type-guessed; the store must still see
        the point as unchanged, or every write re-embeds."""
        org = f"org-{uuid.uuid4().hex[:6]}"
        entity = _entity("2024", EntityType.SUBCATEGORY, org=org, name="2024", level="1", connectors=["c1"], aliases=["FY24"])
        await store.upsert_entities_batch([entity])
        store.vector_db_service.upsert_points = AsyncMock(wraps=store.vector_db_service.upsert_points)
        store.vector_db_service.update_payload_by_ids = AsyncMock(
            wraps=store.vector_db_service.update_payload_by_ids,
        )

        await store.upsert_entities_batch([entity])

        store.vector_db_service.upsert_points.assert_not_awaited()
        store.vector_db_service.update_payload_by_ids.assert_not_awaited()

    async def test_membership_only_change_keeps_text_and_metadata(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        entity = _entity("t1", org=org, name="Release checklist", connectors=["c1"], aliases=["RC"])
        await store.upsert_entities_batch([entity])
        store.vector_db_service.upsert_points = AsyncMock(wraps=store.vector_db_service.upsert_points)

        await store.upsert_entities_batch([_entity("t1", org=org, name="Release checklist", connectors=["c2"], aliases=["RC"])])

        store.vector_db_service.upsert_points.assert_not_awaited()
        payload = await _point(store, org, "topic", "t1")
        assert payload["connectorIds"] == ["c1", "c2"]
        assert payload["page_content"] == "Release checklist"
        assert payload["metadata"]["name"] == "Release checklist"
        assert list(payload["metadata"]["aliases"]) == ["RC"]
        # The vector was kept: the entity is still found by its name.
        await _publish_writes(store)
        (match,) = await _best(store, ["Release checklist"], org, "topic")
        assert match["entityId"] == "t1"

    async def test_updating_a_missing_point_is_ignored(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("t1", org=org, connectors=["c1"])])
        present = store._point_id(org, "topic", "t1")
        missing = store._point_id(org, "topic", "never-written")

        await store.vector_db_service.update_payload_by_ids(
            store.collection_name, [present, missing], {"connectorIds": ["c2"]},
        )

        assert (await _point(store, org, "topic", "t1"))["connectorIds"] == ["c2"]
        assert await _point(store, org, "topic", "never-written") is None


class TestMatchesAndSearch:
    async def test_level_scoped_winner_lookup(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("2024", EntityType.SUBCATEGORY, org=org, name="2024", level="1", connectors=["c1"]),
            _entity("other", EntityType.SUBCATEGORY, org=org, name="Other plan", level="2", connectors=["c1"]),
        ])
        await _publish_writes(store)

        (level_one,) = await _best(store, ["2024"], org, "subcategory", level="1")
        (level_three,) = await _best(store, ["2024"], org, "subcategory", level="3")

        assert level_one == {
            "entityId": "2024", "entityType": "subcategory", "name": "2024",
            "aliases": [], "level": "1", "score": level_one["score"],
        }
        assert level_three is None

    async def test_several_candidates_per_name_best_first(self, store: EntityVectorStore) -> None:
        """KG-12: the resolver offers the top candidates, so each must be a
        distinct entity of the asked org and type, the exact name first."""
        org, other = f"org-{uuid.uuid4().hex[:6]}", f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("checklist", org=org, name="Release checklist", connectors=["c1"]),
            _entity("notes", org=org, name="Release notes", connectors=["c1"]),
            _entity("plan", org=org, name="Release plan", connectors=["c1"]),
            _entity("elsewhere", org=other, name="Release checklist", connectors=["c1"]),
            _entity("legal", EntityType.CATEGORY, org=org, name="Release checklist", connectors=["c1"]),
        ])
        await _publish_writes(store)

        (candidates,) = await store.find_candidates(["Release checklist"], org, "topic", k=3)

        ids = [c["entityId"] for c in candidates]
        assert ids[0] == "checklist"
        assert sorted(ids) == ["checklist", "notes", "plan"]

    async def test_search_is_scoped_by_org_and_membership(self, store: EntityVectorStore) -> None:
        org, other = f"org-{uuid.uuid4().hex[:6]}", f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("mine", org=org, name="Quarterly planning", connectors=["c1"], groups=["g1"]),
            _entity("theirs", org=other, name="Quarterly planning", connectors=["c1"], groups=["g1"]),
            _entity("unreachable", org=org, name="Quarterly planning review", connectors=["c9"], groups=["g9"]),
        ])
        await _publish_writes(store)

        hits = await store.search_entities("quarterly planning", org, {"g1"}, set(), top_k=5)
        wide = await store.search_entities("quarterly planning", org, set(), set(), top_k=5, allow_org_wide=True)

        assert [h["entityId"] for h in hits] == ["mine"]
        assert {h["entityId"] for h in wide} == {"mine", "unreachable"}
        assert all(isinstance(h["entityId"], str) and isinstance(h["name"], str) for h in wide)


class TestConnectorDeletion:
    async def _seed(self, store: EntityVectorStore, org: str, records: int = 0) -> None:
        await store.upsert_entities_batch([
            _entity("shared", org=org, connectors=["A", "B"], groups=["ga", "gb"]),
            _entity("only-a", org=org, connectors=["A"], groups=["ga"]),
            _entity("rg-a", EntityType.RECORD_GROUP, org=org, connectors=["A"], groups=["ga"]),
            _entity("elsewhere", org=f"{org}-other", connectors=["A"], groups=["ga"]),
            *(_entity(f"rec-{i}", EntityType.RECORD, org=org, connectors=["A"], groups=["ga"]) for i in range(records)),
        ], merge_membership=False)
        await _publish_writes(store)

    async def test_strips_shared_and_deletes_the_rest(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await self._seed(store, org, records=3)

        await store.delete_entities_by_connector(org, "A", record_group_ids=["ga"])

        shared = await _point(store, org, "topic", "shared")
        assert (shared["connectorIds"], shared["recordGroupIds"]) == (["B"], ["gb"])
        assert shared["page_content"] == "Name shared"
        assert await _point(store, org, "topic", "only-a") is None
        assert await _point(store, org, "record_group", "rg-a") is None
        assert await _point(store, org, "record", "rec-0") is None
        assert await _point(store, f"{org}-other", "topic", "elsewhere") is not None

    async def test_groups_are_recovered_from_points_when_the_graph_gives_none(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await self._seed(store, org, records=1)

        await store.delete_entities_by_connector(org, "A")

        shared = await _point(store, org, "topic", "shared")
        assert (shared["connectorIds"], shared["recordGroupIds"]) == (["B"], ["gb"])

    async def test_a_point_the_graph_still_links_is_rewritten_not_deleted(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await self._seed(store, org)
        lookup = AsyncMock(return_value={
            ("topic", "only-a"): {"connectorIds": ["C"], "recordGroupIds": ["gc"]},
        })

        await store.delete_entities_by_connector(org, "A", record_group_ids=["ga"], membership_lookup=lookup)

        kept = await _point(store, org, "topic", "only-a")
        assert (kept["connectorIds"], kept["recordGroupIds"]) == (["C"], ["gc"])
        (refs,) = lookup.await_args.args
        assert refs == [{"id": "only-a", "type": "topic"}]

    async def test_many_points_are_paged_from_the_start(self, store: EntityVectorStore) -> None:
        """Every processed point leaves the filter, so the loop never reads
        past the first page (Redis caps search offsets at 10k)."""
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            *(_entity(f"shared-{i}", org=org, connectors=["A", "B"]) for i in range(120)),
            *(_entity(f"only-{i}", org=org, connectors=["A"]) for i in range(130)),
            *(_entity(f"rec-{i}", EntityType.RECORD, org=org, connectors=["A"]) for i in range(150)),
        ], merge_membership=False)
        await _publish_writes(store)
        store.vector_db_service.scroll = AsyncMock(wraps=store.vector_db_service.scroll)

        await store._shrink_connector_membership(org, "A", record_group_ids=[], page_size=50)

        offsets = [c.kwargs.get("offset") for c in store.vector_db_service.scroll.await_args_list]
        taxonomy_scans = [c for c in store.vector_db_service.scroll.await_args_list if c.kwargs["scroll_filter"] is not None]
        assert len(taxonomy_scans) >= 5
        assert all(o is None for o in offsets[1:]), offsets
        await _publish_writes(store)
        remaining = await store.vector_db_service.scroll(
            store.collection_name,
            await store.vector_db_service.filter_collection(must={"metadata.orgId": org}),
            limit=1000,
        )
        ids = sorted(p.payload["metadata"]["entityId"] for p in remaining.points)
        assert ids == sorted(f"shared-{i}" for i in range(120))
        assert all(p.payload["connectorIds"] == ["B"] for p in remaining.points)


class TestDeletesWithoutEmbeddings:
    async def test_record_delete_when_the_embedding_model_is_down(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("r1", EntityType.RECORD, org=org, connectors=["A"])])
        await _publish_writes(store)
        fresh = EntityVectorStore(
            logger=logger, config_service=MagicMock(),
            vector_db_service=store.vector_db_service, collection_name=store.collection_name,
        )
        fresh._init_embeddings = AsyncMock(side_effect=RuntimeError("embedding endpoint down"))  # type: ignore[method-assign]

        await fresh.delete_entity(org, "record", "r1")

        assert await _point(store, org, "record", "r1") is None
        fresh._init_embeddings.assert_not_called()


class TestReplaceMode:
    """Record and record-group points are written with ``merge_membership=False``
    on every indexed record."""

    async def test_unchanged_record_point_is_not_rewritten(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        record = _entity("rec-1", EntityType.RECORD, org=org, name="Q3 plan", connectors=["c1"], groups=["g1"])
        await store.upsert_entities_batch([record], merge_membership=False)
        store.vector_db_service.upsert_points = AsyncMock(wraps=store.vector_db_service.upsert_points)
        store.vector_db_service.update_payload_by_ids = AsyncMock(
            wraps=store.vector_db_service.update_payload_by_ids,
        )

        await store.upsert_entities_batch([record], merge_membership=False)

        store.vector_db_service.upsert_points.assert_not_awaited()
        store.vector_db_service.update_payload_by_ids.assert_not_awaited()

    async def test_moved_record_has_its_group_replaced_without_rewriting(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch(
            [_entity("rec-1", EntityType.RECORD, org=org, name="Q3 plan", connectors=["c1"], groups=["g-old"])],
            merge_membership=False,
        )
        store.vector_db_service.upsert_points = AsyncMock(wraps=store.vector_db_service.upsert_points)
        store.vector_db_service.update_payload_by_ids = AsyncMock(
            wraps=store.vector_db_service.update_payload_by_ids,
        )

        await store.upsert_entities_batch(
            [_entity("rec-1", EntityType.RECORD, org=org, name="Q3 plan", connectors=["c1"], groups=["g-new"])],
            merge_membership=False,
        )

        store.vector_db_service.upsert_points.assert_not_awaited()
        # By id: a search-based update can miss a point not yet refreshed.
        store.vector_db_service.update_payload_by_ids.assert_awaited_once()
        payload = await _point(store, org, "record", "rec-1")
        assert payload["recordGroupIds"] == ["g-new"]
        assert payload["page_content"] == "Q3 plan"


class TestFinalSweep:
    async def test_sweep_spares_taxonomy_and_removes_untyped_points(self, store: EntityVectorStore) -> None:
        """The page loop is skipped so only the sweep acts: a shared topic
        still naming the connector (as after a concurrent re-tag) survives,
        and record-group and untyped points of the connector are removed."""
        from app.services.vector_db.models import VectorPoint

        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("shared", org=org, connectors=["A", "B"], groups=["gb"]),
            _entity("rg-a", EntityType.RECORD_GROUP, org=org, connectors=["A"], groups=["ga"]),
        ], merge_membership=False)
        untyped_id = store._point_id(org, "none", "junk")
        other_untyped_id = store._point_id(org, "none", "other-junk")
        await store.vector_db_service.upsert_points(store.collection_name, [VectorPoint(
            id=untyped_id,
            dense_vector=_StubEmbeddings().embed_query("junk"),
            payload={
                "page_content": "junk",
                "metadata": {"orgId": org, "name": "junk"},
                "connectorIds": ["A"],
                "recordGroupIds": [],
            },
        ), VectorPoint(
            id=other_untyped_id,
            dense_vector=_StubEmbeddings().embed_query("other-junk"),
            payload={
                "page_content": "other-junk",
                "metadata": {"orgId": org, "name": "other-junk"},
                "connectorIds": ["B"],
                "recordGroupIds": [],
            },
        )])
        await _publish_writes(store)
        store._strip_or_delete = AsyncMock(return_value=False)  # type: ignore[method-assign]

        await store.delete_entities_by_connector(org, "A", record_group_ids=["ga"])

        await _publish_writes(store)
        assert await _point(store, org, "topic", "shared") is not None
        assert await _point(store, org, "record_group", "rg-a") is None
        remaining = await store.vector_db_service.retrieve_points(store.collection_name, [untyped_id])
        assert remaining == []
        # Another connector's untyped point is outside the sweep.
        assert await store.vector_db_service.retrieve_points(store.collection_name, [other_untyped_id]) != []


class TestRebuildSupport:
    """What the entity index rebuild relies on, per backend."""

    async def test_points_of_an_org_page_to_the_end(self, store: EntityVectorStore) -> None:
        org, other = f"org-{uuid.uuid4().hex[:6]}", f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch(
            [_entity(f"t{i}", org=org, connectors=["c1"]) for i in range(7)]
            + [_entity("s1", EntityType.SUBCATEGORY, org=org, level="2", connectors=["c1"]),
               _entity("t-other", org=other, connectors=["c1"])],
        )
        await store.upsert_entities_batch(
            [_entity("g1", EntityType.RECORD_GROUP, org=org, connectors=["c1"], groups=["g1"]),
             _entity("r1", EntityType.RECORD, org=org, connectors=["c1"])],
            merge_membership=False,
        )
        await _publish_writes(store)

        refs: list[EntityPointRef] = []
        offset, pages = None, 0
        while True:
            page, offset = await store.page_entity_points(
                org, ["topic", "subcategory", "record_group"], offset=offset, limit=3,
            )
            refs.extend(page)
            pages += 1
            if offset is None:
                break
            assert pages < 10, "paging did not terminate"

        assert sorted((r.entity_type, r.entity_id, r.level) for r in refs) == sorted(
            [("topic", f"t{i}", None) for i in range(7)]
            + [("subcategory", "s1", "2"), ("record_group", "g1", None)]
        )

    async def test_paging_on_after_deleting_a_page_reaches_every_remaining_point(
        self, store: EntityVectorStore,
    ) -> None:
        """What the sweep does: read a page, delete its points, page on from
        the adjusted offset. A positional cursor (Redis) left unadjusted skips
        as many points as were deleted."""
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity(f"t{i}", org=org, connectors=["c1"]) for i in range(7)])
        await _publish_writes(store)

        first, offset = await store.page_entity_points(org, ["topic"], limit=3)
        assert len(first) == 3 and offset is not None
        await store.delete_entities(org, "topic", [r.entity_id for r in first])
        await _publish_writes(store)
        offset = store.offset_after_delete(offset, len(first))

        rest: list[EntityPointRef] = []
        while offset is not None:
            page, offset = await store.page_entity_points(org, ["topic"], offset=offset, limit=3)
            rest.extend(page)
        assert sorted(r.entity_id for r in rest) == sorted(
            {f"t{i}" for i in range(7)} - {r.entity_id for r in first}
        )

    async def test_delete_entities_removes_only_the_named_points(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("t1", org=org, connectors=["c1"]),
            _entity("t2", org=org, connectors=["c1"]),
            _entity("t1", EntityType.CATEGORY, org=org, connectors=["c1"]),
        ])
        await _publish_writes(store)
        await store.delete_entities(org, "topic", ["t1"])
        assert await _point(store, org, "topic", "t1") is None
        assert await _point(store, org, "topic", "t2") is not None
        assert await _point(store, org, "category", "t1") is not None

    async def test_a_model_change_reembeds_and_the_same_model_does_not(self, store: EntityVectorStore) -> None:
        """Redis hands the recorded model back as a flattened string field;
        it must still compare equal, or every write re-embeds."""
        org = f"org-{uuid.uuid4().hex[:6]}"
        entity = _entity("t1", org=org, connectors=["c1"])
        await store.upsert_entities_batch([entity], merge_membership=False)
        store.vector_db_service.upsert_points = AsyncMock(wraps=store.vector_db_service.upsert_points)
        await store.upsert_entities_batch([entity], merge_membership=False)
        store.vector_db_service.upsert_points.assert_not_awaited()

        store._model_id = "other:model"
        assert (await store.upsert_entities_batch([entity], merge_membership=False)).written == 1
        store.vector_db_service.upsert_points.assert_awaited_once()
        payload = await _point(store, org, "topic", "t1")
        assert payload["metadata"]["embeddingModel"] == f"other:model:{DIM}"

    async def test_dimension_change_recreates_only_when_enabled(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("t1", org=org, connectors=["c1"])])

        def _wider(recreate: bool) -> EntityVectorStore:
            wider = EntityVectorStore(
                logger=logger, config_service=MagicMock(), vector_db_service=store.vector_db_service,
                collection_name=store.collection_name, recreate_on_dimension_mismatch=recreate,
            )

            async def _stub() -> None:
                wider._dense_embeddings = _StubEmbeddings()
                wider._embedding_size = DIM * 2

            wider._init_embeddings = _stub  # type: ignore[method-assign]
            return wider

        with pytest.raises(VectorStoreError):
            await _wider(False)._ensure_initialized()
        assert await _point(store, org, "topic", "t1") is not None

        await _wider(True)._ensure_initialized()
        info = await store.vector_db_service.get_collection_info(store.collection_name)
        assert info.exists and info.dense_dimension == DIM * 2
        assert await _point(store, org, "topic", "t1") is None

        # A service initialised before the recreation still holds the old
        # model: once the rebuild has written to the new collection its search
        # fails (Redis answers an empty index without checking the vector),
        # and the store resets to pick up the new model.
        from app.services.vector_db.models import VectorPoint

        await store.vector_db_service.upsert_points(store.collection_name, [VectorPoint(
            id=store._point_id(org, "topic", "t2"), dense_vector=[0.1] * (DIM * 2),
            payload={"page_content": "pricing", "metadata": {"orgId": org, "name": "pricing"},
                     "connectorIds": ["c1"], "recordGroupIds": []},
        )])
        await _publish_writes(store)
        with pytest.raises(Exception):
            await store.search_entities("pricing", org, set(), {"c1"})
        assert store._initialized is False

class TestWriteOutcomeAndLocks:
    """Embedding outside the locks, and the reported outcome, per backend."""

    async def test_outcome_on_a_real_backend(self, store: EntityVectorStore) -> None:
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("same", org=org, connectors=["c1"]),
                                           _entity("moved", org=org, connectors=["c1"])], merge_membership=False)
        outcome = await store.upsert_entities_batch(
            [_entity("same", org=org, connectors=["c1"]), _entity("moved", org=org, connectors=["c2"]),
             _entity("new", org=org, connectors=["c1"])],
            merge_membership=False,
        )
        assert (outcome.written, outcome.membership_only, outcome.unchanged, outcome.failed) == (1, 1, 1, 0)
        assert (await _point(store, org, "topic", "moved"))["connectorIds"] == ["c2"]

    async def test_concurrent_writers_on_one_entity_keep_both_memberships(self, store: EntityVectorStore) -> None:
        import asyncio

        org = f"org-{uuid.uuid4().hex[:6]}"
        writers = [_entity("eng", EntityType.DEPARTMENT, org=org, connectors=[f"c{i}"]) for i in range(6)]
        await asyncio.gather(*(store.upsert_entities_batch([w]) for w in writers))
        payload = await _point(store, org, "department", "eng")
        assert sorted(payload["connectorIds"]) == [f"c{i}" for i in range(6)]

    async def test_cleanup_keeps_a_connector_added_after_the_scroll(self, store: EntityVectorStore) -> None:
        """KG-34 residue: the strip phase re-reads by id under the lock, so a
        connector indexed between the page scroll and the write survives."""
        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([_entity("t1", org=org, connectors=["c-gone", "c-other"])])
        await _publish_writes(store)
        service = store.vector_db_service
        real_retrieve = service.retrieve_points
        added = False

        async def _retrieve_after_a_concurrent_index(collection: str, ids: list[str]) -> list:
            nonlocal added
            if not added:
                added = True
                await service.update_payload_by_ids(
                    collection, [store._point_id(org, "topic", "t1")],
                    {"connectorIds": ["c-gone", "c-other", "c-new"]},
                )
            return await real_retrieve(collection, ids)

        service.retrieve_points = _retrieve_after_a_concurrent_index
        try:
            await store.delete_entities_by_connector(org, "c-gone", record_group_ids=[])
        finally:
            service.retrieve_points = real_retrieve
        payload = await _point(store, org, "topic", "t1")
        assert payload["connectorIds"] == ["c-other", "c-new"]


class TestSearchPasses:
    async def test_passes_answer_separately_in_one_request(self, store: EntityVectorStore) -> None:
        """KG-08: every pass in one vector request, each with its own scope."""
        from app.modules.transformers.entity_vectorstore import EntitySearchPass

        org = f"org-{uuid.uuid4().hex[:6]}"
        await store.upsert_entities_batch([
            _entity("by-group", org=org, name="Quarterly planning", connectors=["c9"], groups=["g1"]),
            _entity("by-connector", org=org, name="Quarterly planning notes", connectors=["c1"]),
            _entity("unscoped", org=org, name="Quarterly planning review", connectors=[]),
        ])
        await _publish_writes(store)
        group_pass, connector_pass, wide_pass = await store.search_entities_passes(
            "quarterly planning", org,
            [
                EntitySearchPass(frozenset({"g1"}), frozenset()),
                EntitySearchPass(frozenset(), frozenset({"c1"})),
                EntitySearchPass(org_wide=True),
            ],
        )
        assert {h["entityId"] for h in group_pass} == {"by-group"}
        assert {h["entityId"] for h in connector_pass} == {"by-connector"}
        assert {h["entityId"] for h in wide_pass} == {"by-group", "by-connector", "unscoped"}

    async def test_record_titles_do_not_crowd_out_taxonomy_entities(self, store: EntityVectorStore) -> None:
        """KG-14: titles outnumber taxonomy nodes by orders of magnitude; with
        one pool a query word shared by many titles pushed the topics out."""
        from app.modules.transformers.entity_vectorstore import EntitySearchPass

        org = f"org-{uuid.uuid4().hex[:6]}"
        titles = [
            _entity(f"t{i}", EntityType.RECORD, org=org, name=f"Security review notes {i}", connectors=["c1"])
            for i in range(12)
        ]
        await store.upsert_entities_batch([
            *titles,
            _entity("sec-topic", org=org, name="Security", connectors=["c1"]),
            _entity("audit-topic", org=org, name="Security audit", connectors=["c1"]),
        ])
        await _publish_writes(store)

        (hits,) = await store.search_entities_passes(
            "security review", org, [EntitySearchPass(org_wide=True)], top_k=4,
        )

        types = [h["entityType"] for h in hits]
        assert {"sec-topic", "audit-topic"} <= {h["entityId"] for h in hits}
        assert types.count("record") == 2  # top_k split evenly between the two requests
