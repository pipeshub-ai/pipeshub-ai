"""Labels shown to a user come only from records they can open.

Two records share one canonical topic and category: the first one indexed
names the nodes. A user who can open only the other record must never see a
spelling that exists only in the first, whichever was indexed first: not in
that record's metadata, not in any rendered record text, and not in the
knowledge-graph tools' output.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.actions.knowledge_graph.ops.entity_discovery import execute_search_entities
from app.agents.actions.knowledge_graph.ops.entity_records import execute_find_records_by_entity
from app.agents.actions.knowledge_graph.ops.search import resolve_entity_filter_groups
from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityType
from app.modules.agents.record_escalation.policy import build_candidates
from app.modules.agents.record_escalation.renderer import render_candidate_table
from app.services.graph_db.common.utils import PermittedEntityRows
from tests.support.fake_entity_graph import RECORDS, FakeGraph
from tests.unit.modules.entity_resolution.conftest import FakeEntityVectorStore, _tokens

ORG = "acme"
CONNECTOR = "conn-1"
USER_ID = "user-b"
USER_KEY = "user-b-key"
TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value

RESTRICTED = "restricted-brief"
OPEN = "open-plan"
RESTRICTED_ONLY_WORDS = ("falcon", "codename")

_ENTITY_COLLECTIONS = {
    "category": (CATEGORIES,),
    "subcategory": (
        CollectionNames.SUBCATEGORIES1.value,
        CollectionNames.SUBCATEGORIES2.value,
        CollectionNames.SUBCATEGORIES3.value,
    ),
    "topic": (TOPICS,),
    "language": (CollectionNames.LANGUAGES.value,),
}


class ScopedGraph(FakeGraph):
    """The fake graph plus the read paths the knowledge-graph tools use, with
    one user who can open only ``readable`` records of one connector."""

    def __init__(self) -> None:
        super().__init__()
        self.readable: set[str] = set()

    async def get_entity_access_context(self, user_id, org_id, source_ids=None, *, exclude_app_ids=()) -> dict:
        assert user_id == USER_ID and org_id == ORG
        return {
            "user_key": USER_KEY,
            "apps": [{"id": CONNECTOR, "name": "Drive", "type": "DRIVE", "permissionModel": "RECORD_LEVEL"}],
            "record_group_ids": [],
        }

    def _linked_records(self, entity_type: str, entity_id: str) -> list[str]:
        targets = {f"{c}/{entity_id}" for c in _ENTITY_COLLECTIONS.get(entity_type, ())}
        keys = {
            frm.split("/", 1)[1]
            for (_c, frm, to) in self.edges
            if to in targets and frm.startswith(f"{RECORDS}/")
        }
        return sorted(keys)

    def _row(self, key: str) -> dict[str, Any]:
        record = self.records[key]
        return {
            "_key": key,
            "recordName": record["recordName"],
            "recordType": "FILE",
            "connectorId": record["connectorId"],
            "virtualRecordId": f"vr-{key}",
            "webUrl": None,
            "hideWeburl": False,
            "sourceLastModifiedTimestamp": 1,
            "updatedAtTimestamp": 1,
        }

    async def get_permitted_entity_records(
        self, refs, org_id, user_key, *, app_level_connector_ids, record_types=None,
        limit_per_entity=20, offset=0, window=200, timeout_seconds=None,
    ) -> dict[tuple[str, str], PermittedEntityRows]:
        assert user_key == USER_KEY
        out: dict[tuple[str, str], PermittedEntityRows] = {}
        for ref in refs:
            candidates = self._linked_records(ref["type"], ref["id"])[offset:offset + window]
            permitted = [self._row(k) for k in candidates if k in self.readable][:limit_per_entity]
            out[(ref["type"], ref["id"])] = PermittedEntityRows(
                permitted, window_size=len(candidates), examined=len(candidates),
            )
        return out


class SearchableStore(FakeEntityVectorStore):
    async def search_entities_passes(
        self, query, org_id, passes, *, entity_types=None, top_k=10, **_kwargs,
    ) -> list[list[dict[str, Any]]]:
        hits = []
        for (point_org, entity_type, _key), point in sorted(self.points.items()):
            if point_org != org_id or (entity_types and entity_type not in entity_types):
                continue
            words = _tokens(point["name"]) | {t for a in point["aliases"] for t in _tokens(a)}
            if _tokens(query) & words:
                hits.append({**point, "score": 0.9})
        return [hits[:top_k] for _ in passes]


@pytest.fixture
def fake_graph() -> ScopedGraph:
    return ScopedGraph()


@pytest.fixture
def fake_store(fake_graph) -> SearchableStore:
    return SearchableStore(fake_graph)


def _restricted_metadata(metadata_factory) -> Any:
    return metadata_factory(
        categories=["Codename Falcon programme"],
        topics=["Falcon launch window"],
        summary="Restricted planning brief.",
    )


def _open_metadata(metadata_factory) -> Any:
    return metadata_factory(
        categories=["Product programme"],
        topics=["Product launch window"],
        summary="Shared launch plan.",
    )


SCRIPTS = {
    # The second record's names are judged the same concept as the first's.
    "restricted_first": {
        "product programme": ("same", "Codename Falcon programme"),
        "product launch window": ("same", "Falcon launch window"),
    },
    "open_first": {
        "codename falcon programme": ("same", "Product programme"),
        "falcon launch window": ("same", "Product launch window"),
    },
}


async def _index(record_id, meta, *, make_resolver, make_transformer, fake_graph, fake_store, ctx_factory) -> Any:
    fake_graph.add_record(record_id, ORG, CONNECTOR)
    fake_graph.records[record_id]["recordName"] = (
        "Restricted brief" if record_id == RESTRICTED else "Shared launch plan"
    )
    ctx = ctx_factory(record_id, ORG, meta, connector_id=CONNECTOR)
    await make_resolver("apply").resolve(ctx)
    touched = await make_transformer().apply(ctx)
    await fake_store.upsert_entities_batch(touched)
    return ctx


def _assert_no_restricted_words(text: str, where: str) -> None:
    lowered = text.casefold()
    leaked = [w for w in RESTRICTED_ONLY_WORDS if w in lowered]
    assert not leaked, f"{where} shows {leaked}: {text}"


@pytest.mark.parametrize("order", ["restricted_first", "open_first"])
class TestLabelsShownToAUserComeOnlyFromRecordsTheyCanOpen:
    @pytest.fixture
    async def indexed(
        self, order, make_resolver, make_transformer, fake_graph, fake_store,
        metadata_factory, ctx_factory, scripted_model,
    ) -> dict[str, Any]:
        scripted_model(SCRIPTS[order])
        kwargs = dict(
            make_resolver=make_resolver, make_transformer=make_transformer,
            fake_graph=fake_graph, fake_store=fake_store, ctx_factory=ctx_factory,
        )
        first, second = (RESTRICTED, OPEN) if order == "restricted_first" else (OPEN, RESTRICTED)
        metas = {RESTRICTED: _restricted_metadata(metadata_factory), OPEN: _open_metadata(metadata_factory)}
        ctxs = {}
        ctxs[first] = await _index(first, metas[first], **kwargs)
        ctxs[second] = await _index(second, metas[second], **kwargs)
        fake_graph.readable = {OPEN}
        # Both records share one canonical topic and one category.
        assert len(fake_graph.nodes_in(TOPICS)) == 1
        assert len(fake_graph.nodes_in(CATEGORIES)) == 1
        return ctxs

    def _state(self, fake_graph, fake_store) -> dict[str, Any]:
        return {
            "org_id": ORG,
            "user_id": USER_ID,
            "graph_provider": fake_graph,
            "entity_vector_store": fake_store,
            "apps": [CONNECTOR],
        }

    async def test_record_metadata_and_rendered_text(self, indexed) -> None:
        meta = indexed[OPEN].record.semantic_metadata
        assert meta.topics == ["Product launch window"]
        assert meta.categories == ["Product programme"]
        _assert_no_restricted_words(json.dumps(meta.model_dump()), "record metadata")
        _assert_no_restricted_words("\n".join(meta.to_llm_context()), "record context")

        plan = build_candidates(
            coverage={OPEN: (1, 4)},
            records_in_relevance_order=[{
                "id": OPEN, "record_name": "Shared launch plan",
                "semantic_metadata": meta.model_dump(),
            }],
            already_fetched_ids=set(),
        )
        table = render_candidate_table(plan)
        assert "Topics: Product launch window" in table
        _assert_no_restricted_words(table, "candidate table")

    async def test_search_entities_and_the_records_it_lists(self, indexed, fake_graph, fake_store) -> None:
        state = self._state(fake_graph, fake_store)
        ok, text = await execute_search_entities(state, "launch programme")
        assert ok, text
        results = json.loads(text)["results"]
        shown = {r["entityType"]: r["name"] for r in results}
        assert shown == {"topic": "Product launch window", "category": "Product programme"}
        _assert_no_restricted_words(text, "search_entities")

        for result in results:
            ok, listing = await execute_find_records_by_entity(state, result["entityId"])
            assert ok, listing
            assert "Shared launch plan" in listing
            _assert_no_restricted_words(listing, "find_records_by_entity")

    async def test_entity_filters_still_reach_the_canonical_node(self, indexed, fake_graph, fake_store) -> None:
        state = self._state(fake_graph, fake_store)
        ok, text = await execute_search_entities(state, "launch", entity_types=["topic"])
        assert ok, text
        (topic,) = json.loads(text)["results"]
        (node,) = fake_graph.nodes_in(TOPICS)
        # The graph filter matches the node's stored name; it is never shown.
        assert resolve_entity_filter_groups(state, [topic["entityId"]]) == {"topics": [node["name"]]}

    async def test_entity_points_keep_every_spelling_for_the_merge_model(self, indexed, fake_store) -> None:
        (point,) = fake_store.points_of(ORG, EntityType.TOPIC.value)
        spellings = {point["name"], *point["aliases"]}
        assert spellings == {"Falcon launch window", "Product launch window"}
