"""P2.13 RS-1..RS-9: the retrieval scope is (actor ∩ grant) ∪ the actor's publications, applied to the vector filter.

The search runs through ``RetrievalService.search_with_filters`` with the graph and vector layers mocked at the
``IGraphDBProvider`` / ``IVectorDBService`` boundary. Publications are real (``KnowledgePublications`` over the
in-memory directory, its audience, expiry and revocation rules); only how a container's records are listed is
stubbed (the real-backend half is ``tests/integration/graph_db/test_knowledge_publications.py``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.fastapi_responses import Status
from app.harness.knowledge.service import Container
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import Audience, Principal, PublicationDraft

ORG = "o1"
NOW = 1_000_000
KB = "apps/kb-hr"
KB_RECORDS = {"v-pub-1": "r-pub-1", "v-pub-2": "r-pub-2"}
PRINCIPALS = {
    "alice": Principal(ORG, "alice", frozenset({"t-hr"})),
    "bob": Principal(ORG, "bob"),
    "carol": Principal(ORG, "carol", frozenset({"t-hr"})),
    "creator": Principal(ORG, "creator"),
}


@pytest.fixture(autouse=True)
def _clear_user_cache():  # noqa: ANN202
    mod._user_cache.clear()
    yield
    mod._user_cache.clear()


@pytest.fixture(autouse=True)
def _no_locations():  # noqa: ANN202
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations", AsyncMock(return_value={})
    ):
        yield


class World:
    def __init__(self, service: Any, graph: Any, vector: Any) -> None:  # noqa: ANN401
        self.service = service
        self.graph = graph
        self.vector = vector
        self.directory = InMemoryPublicationDirectory()
        self.now = NOW
        self.enabled = True
        self.containers: dict[str, dict[str, str]] = {KB: dict(KB_RECORDS)}
        self.own: dict[str, dict[str, str]] = {}
        pubs = service.publications
        pubs.directory = self.directory
        pubs._now = lambda: self.now
        pubs.enabled = self._enabled
        pubs.principal = self._principal
        pubs.container = self._container
        pubs.container_records = self._records
        graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=self._own)
        graph.get_records_by_record_ids = AsyncMock(side_effect=self._records_by_ids)
        # Main's per-hit check (#4255) answers from the same own maps: a user's graph key is their id here.
        graph.get_user_by_user_id = AsyncMock(side_effect=lambda user_id, **_: {"_key": user_id, "id": user_id})
        graph.check_access = AsyncMock(side_effect=self._check)

    async def _enabled(self) -> bool:
        return self.enabled

    async def _principal(self, user_id: str | None, org_id: str) -> Principal | None:
        return PRINCIPALS.get(user_id or "")

    async def _container(self, org_id: str, qualified: str) -> Container | None:
        if qualified not in self.containers or org_id != ORG:
            return None
        collection, key = qualified.split("/", 1)
        return Container(qualified, collection, key, key)

    async def _records(self, org_id: str, container: Container, cap: int) -> dict[str, str]:
        return dict(list(self.containers.get(container.id, {}).items())[:cap])

    async def _own(self, user_id: str, org_id: str, **_: Any) -> dict[str, str]:  # noqa: ANN401
        return dict(self.own.get(user_id, {}))

    async def _check(
        self, user_key: str, org_id: str, *, node_ids: Any = (), virtual_record_ids: Any = (), **_: Any  # noqa: ANN401
    ) -> AccessCheck:
        own = self.own.get(user_key, {}) if org_id == ORG else {}
        readable = frozenset(rid for rid in node_ids if rid in own.values())
        return AccessCheck(
            node_ids=readable,
            records_by_vrid={vrid: own[vrid] for vrid in virtual_record_ids if vrid in own},
            node_ids_in_scope=readable,
        )

    async def _records_by_ids(self, record_ids: list[str], org_id: str) -> list[dict[str, Any]]:
        return [
            {"_key": rid, "virtualRecordId": "v" + rid[1:], "origin": "CONNECTOR", "recordName": rid,
             "mimeType": "text/plain", "connectorName": "KB", "webUrl": "http://x/" + rid}
            for rid in record_ids
        ]

    async def publish(self, audience: Audience, mode: str = "answer_only", expires: int | None = None, pid: str = "p1") -> None:
        draft = PublicationDraft(ORG, KB, audience, mode, "owner", expires)  # type: ignore[arg-type]
        await self.directory.create(draft, publication_id=pid, now_ms=NOW)

    async def search(self, user_id: str, **kwargs: Any) -> tuple[set[str], dict[str, dict[str, Any]], Any]:  # noqa: ANN401
        vids = sorted({*self.own.get(user_id, {}), *KB_RECORDS, *self.own.get(kwargs.get("caller_user_id") or "", {})})
        self.service._execute_parallel_searches = AsyncMock(
            return_value=[{"score": 0.9, "content": "c", "citationType": "vectordb|document",
                           "metadata": {"virtualRecordId": v, "orgId": ORG}} for v in vids]
        )
        self.vector.filter_collection.reset_mock()
        result = await self.service.search_with_filters(queries=["q"], user_id=user_id, org_id=ORG, **kwargs)
        filtered: set[str] = set()
        for call in self.vector.filter_collection.await_args_list:
            must = call.kwargs.get("must") or {}
            filtered |= set(must.get("virtualRecordId") or [])
        returned = {r["metadata"]["virtualRecordId"]: r["metadata"] for r in result.get("searchResults", [])}
        return filtered, returned, result


@pytest.fixture
def world(logger, mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store) -> World:
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
        from app.modules.retrieval.retrieval_service import RetrievalService

        registry = MagicMock()
        registry.strategy_name = "single"
        service = RetrievalService(
            logger=logger, config_service=mock_config_service, collection_registry=registry,
            vector_db_service=mock_vector_db_service, graph_provider=mock_graph_provider, blob_store=mock_blob_store,
        )
    return World(service, mock_graph_provider, mock_vector_db_service)


async def test_rs1_an_outsider_never_gets_published_records(world: World) -> None:
    world.own["bob"] = {"v-bob": "r-bob"}
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    filtered, returned, _ = await world.search("bob")
    assert filtered == {"v-bob"}
    assert set(returned) == {"v-bob"}


@pytest.mark.parametrize(
    ("audience", "reader"),
    [
        (Audience(user_ids=frozenset({"alice"})), "alice"),
        (Audience(group_ids=frozenset({"t-hr"})), "carol"),
        (Audience(org=True), "bob"),
    ],
)
async def test_rs2_an_audience_member_is_answered_from_published_records(world: World, audience: Audience, reader: str) -> None:
    world.own[reader] = {"v-own": "r-own"}
    await world.publish(audience)
    filtered, returned, result = await world.search(reader)
    assert filtered == {"v-own", *KB_RECORDS}
    assert set(returned) == {"v-own", *KB_RECORDS}
    # The record map fetch_full_record reads carries it too (MD-1).
    assert result["virtual_to_record_map"]["v-pub-1"]["publicationMode"] == "answer_only"
    assert "publicationMode" not in result["virtual_to_record_map"]["v-own"]
    for vid in KB_RECORDS:
        assert (returned[vid]["publicationId"], returned[vid]["publicationMode"]) == ("p1", "answer_only")
    assert "publicationId" not in returned["v-own"]


async def test_rs3_own_access_wins_over_a_publication(world: World) -> None:
    world.own["alice"] = {"v-pub-1": "r-pub-1"}
    await world.publish(Audience(user_ids=frozenset({"alice"})), mode="full")
    _, returned, _ = await world.search("alice")
    assert "publicationId" not in returned["v-pub-1"]
    assert returned["v-pub-2"]["publicationMode"] == "full"


async def test_rs4_the_grant_narrows_publications(world: World) -> None:
    world.own["alice"] = {"v-own": "r-own"}
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    filtered, _, _ = await world.search("alice", filter_groups={"kb": ["kb-other"]})
    assert filtered == {"v-own"}
    filtered, _, _ = await world.search("alice", filter_groups={"kb": ["kb-hr"]})
    assert filtered == {"v-own", *KB_RECORDS}
    # A record-level filter cannot be applied to a container: nothing published (narrower, never wider).
    filtered, _, _ = await world.search("alice", filter_groups={"departments": ["d1"]})
    assert filtered == {"v-own"}


async def test_rs5_expiry_and_revocation_act_at_the_next_search(world: World) -> None:
    world.own["alice"] = {"v-own": "r-own"}
    await world.publish(Audience(user_ids=frozenset({"alice"})), expires=NOW + 10)
    assert (await world.search("alice"))[0] == {"v-own", *KB_RECORDS}
    world.now = NOW + 10
    assert (await world.search("alice"))[0] == {"v-own"}
    world.now = NOW
    assert (await world.search("alice"))[0] == {"v-own", *KB_RECORDS}
    await world.directory.revoke(ORG, "p1", by="owner", now_ms=NOW + 1)
    assert (await world.search("alice"))[0] == {"v-own"}


async def test_rs6_an_agent_retrieving_as_its_creator_answers_the_caller_only(world: World) -> None:
    world.own["creator"] = {"v-shared": "r-shared", "v-secret": "r-secret", **KB_RECORDS}
    world.own["bob"] = {"v-shared": "r-shared", "v-bob-only": "r-bob-only"}
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    filtered, returned, _ = await world.search("creator", caller_user_id="bob")
    # Bob: what he and the creator can both read; not the creator's own secret, not his own beyond the creator.
    assert filtered == {"v-shared"}
    assert set(returned) == {"v-shared"}
    filtered, returned, _ = await world.search("creator", caller_user_id="alice")
    assert filtered == set(KB_RECORDS)
    assert {m["publicationId"] for m in returned.values()} == {"p1"}


async def test_rs6_an_unresolvable_caller_gets_nothing(world: World) -> None:
    world.own["creator"] = {"v-secret": "r-secret"}
    filtered, returned, result = await world.search("creator", caller_user_id="slack-bot")
    assert filtered == set()
    assert returned == {}
    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value


async def test_rs6_the_creator_reading_for_themself_is_not_delegated(world: World) -> None:
    world.own["creator"] = {"v-secret": "r-secret"}
    filtered, _, _ = await world.search("creator", caller_user_id="creator")
    assert filtered == {"v-secret"}


async def test_rs7_flag_off_reads_no_publication_and_searches_as_today(world: World) -> None:
    world.enabled = False
    world.own["alice"] = {"v-own": "r-own"}
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    world.directory.list_for_org = AsyncMock(side_effect=AssertionError("read with the flag off"))  # type: ignore[method-assign]
    filtered, returned, _ = await world.search("alice")
    assert filtered == {"v-own"}
    assert "publicationId" not in returned["v-own"]
    # A service-account search is today's creator scope with the flag off.
    world.own["creator"] = {"v-secret": "r-secret"}
    assert (await world.search("creator", caller_user_id="bob"))[0] == {"v-secret"}


async def test_rs8_a_publication_takes_the_record_id_path(world: World, mock_graph_provider: Any) -> None:  # noqa: ANN401
    world.own["alice"] = {"v-own": "r-own"}
    # The container filter is always on since main #4255: only a publication sends a search down the record-id path.
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    await world.search("alice")
    mock_graph_provider.get_accessible_containers.assert_not_awaited()
    await world.search("bob")
    mock_graph_provider.get_accessible_containers.assert_awaited()


async def test_rs9_a_publication_of_a_deleted_container_grants_nothing(world: World) -> None:
    world.own["alice"] = {"v-own": "r-own"}
    await world.publish(Audience(user_ids=frozenset({"alice"})))
    del world.containers[KB]
    assert (await world.search("alice"))[0] == {"v-own"}
