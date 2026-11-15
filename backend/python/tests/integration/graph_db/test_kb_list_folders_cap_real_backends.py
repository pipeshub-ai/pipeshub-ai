"""The collection list returns at most 100 folders per collection, the first by id, and says when there are more.

PostgreSQL runs the same cases in tests/integration/graph_db/postgres/test_kb.py.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.services.graph_db.common.utils import KB_LIST_FOLDERS_CAP
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_list_folders_cap_it"

logger = logging.getLogger("kb-list-folders-cap-it")


@dataclass
class _Org:
    service: KnowledgeBaseService
    org_id: str
    owner_id: str
    folders: dict[str, list[str]]       # collection id -> its folder ids


async def _remove(graph: IGraphDBProvider, ids: dict[str, list[str]]) -> None:
    for collection, keys in ids.items():
        if keys:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges(keys, collection)


@pytest.fixture(params=["neo4j", "arango"])
async def org(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Org]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-kbcap-{run}"
        owner_id, owner_key = f"owner-kbcap-{run}", f"okey-kbcap-{run}"
        seeded: dict[str, list[str]] = {
            CollectionNames.ORGS.value: [org_id],
            CollectionNames.USERS.value: [owner_key],
            CollectionNames.APPS.value: [],
            CollectionNames.RECORDS.value: [],
            CollectionNames.FILES.value: [],
        }
        cleanup.push_async_callback(_remove, graph, seeded)
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "individual", "isActive": True, "name": "kb cap"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": owner_key, "userId": owner_id, "orgId": org_id, "email": f"{owner_id}@example.com",
              "isActive": True}],
            CollectionNames.USERS.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id

        async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
            return processor

        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=processor_for_kb)

        folders: dict[str, list[str]] = {}
        for label, count in (("over", KB_LIST_FOLDERS_CAP + 1), ("at", KB_LIST_FOLDERS_CAP), ("empty", 0)):
            created = await service.create_knowledge_base(user_id=owner_id, org_id=org_id, name=f"kbcap {label} {run}")
            assert created and created.get("success") is not False, created
            kb_id = created["id"]
            seeded[CollectionNames.APPS.value].append(kb_id)
            ids = [str(uuid.uuid4()) for _ in range(count)]
            if ids:
                # The write a folder created in the collection's root goes through (create_folder_in_kb).
                await processor.on_new_records(
                    [(service._build_kb_folder_record(kb_id, i, f"folder {i}", org_id, parent_folder_id=None), [])
                     for i in ids]
                )
            seeded[CollectionNames.RECORDS.value] += ids
            seeded[CollectionNames.FILES.value] += ids
            folders[kb_id] = ids

        yield _Org(service, org_id, owner_id, folders)


async def test_a_collection_lists_its_first_100_folders_by_id_and_says_when_there_are_more(org: _Org) -> None:
    result = await org.service.list_user_knowledge_bases(user_id=org.owner_id, org_id=org.org_id, page=1, limit=50)

    assert isinstance(result, dict) and "knowledgeBases" in result, result
    listed = {kb["id"]: kb for kb in result["knowledgeBases"]}
    assert set(listed) == set(org.folders)
    for kb_id, ids in org.folders.items():
        kb = listed[kb_id]
        assert [f["id"] for f in kb["folders"]] == sorted(ids)[:KB_LIST_FOLDERS_CAP], f"{len(ids)} folders"
        assert kb["foldersTruncated"] is (len(ids) > KB_LIST_FOLDERS_CAP), f"{len(ids)} folders"


async def test_a_page_of_one_collection_reads_the_same_folders_as_the_whole_list(org: _Org) -> None:
    """The folders are read for the page of collections only (after it is cut): one collection at a time gives each the same folders and flag as one page of all."""
    whole = await org.service.list_user_knowledge_bases(user_id=org.owner_id, org_id=org.org_id, page=1, limit=50)
    by_id = {kb["id"]: (kb["folders"], kb["foldersTruncated"]) for kb in whole["knowledgeBases"]}
    paged = {}
    for page in range(1, len(org.folders) + 1):
        one = await org.service.list_user_knowledge_bases(user_id=org.owner_id, org_id=org.org_id, page=page, limit=1)
        assert len(one["knowledgeBases"]) == 1, one
        kb = one["knowledgeBases"][0]
        paged[kb["id"]] = (kb["folders"], kb["foldersTruncated"])
    assert paged == by_id
