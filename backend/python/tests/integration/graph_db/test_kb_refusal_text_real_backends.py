"""A knowledge-base refusal never quotes an id that matched nothing, against real Neo4j and ArangoDB.

Node shows the provider's ``reason`` to the client and logs it. An unknown knowledge base or folder id
can be anything the client typed, so the reason leaves it out; a folder of another knowledge base is
not named either. The PostgreSQL twin is in postgres/test_kb.py. Environment: NEO4J_IT_URI,
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
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_refusal_text_it"
EMAIL = "ada.lovelace@example.com"
KB_NOT_FOUND = {"success": False, "code": 404, "reason": "Knowledge base not found"}

logger = logging.getLogger("kb-refusal-text-it")


@dataclass
class _World:
    service: KnowledgeBaseService
    org_id: str
    owner: str  # userId
    outsider: str  # userId, no role on any knowledge base
    kb_id: str
    kb_name: str
    foreign_folder: str  # a folder of another knowledge base


async def _remove(graph: IGraphDBProvider, ids: dict[str, list[str]]) -> None:
    for collection, keys in ids.items():
        if keys:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges(keys, collection)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-kbtext-{run}"
        owner, outsider = (f"{who}-kbtext-{run}" for who in ("owner", "outsider"))
        seeded: dict[str, list[str]] = {
            CollectionNames.ORGS.value: [org_id],
            CollectionNames.USERS.value: [f"key-{owner}", f"key-{outsider}"],
            CollectionNames.APPS.value: [],
            CollectionNames.RECORDS.value: [],
        }
        cleanup.push_async_callback(_remove, graph, seeded)
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb text"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": f"key-{u}", "userId": u, "orgId": org_id, "email": f"{u}@example.com", "isActive": True}
             for u in (owner, outsider)],
            CollectionNames.USERS.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id

        async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
            return processor

        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=processor_for_kb)
        made: dict[str, tuple[str, str]] = {}
        for label in ("docs", "other"):
            name = f"kbtext {label} {run}"
            kb = await service.create_knowledge_base(user_id=owner, org_id=org_id, name=name)
            assert kb and kb.get("success") is not False, kb
            seeded[CollectionNames.APPS.value].append(kb["id"])
            folder = await service.create_folder_in_kb(kb["id"], f"secret {label}", owner, org_id)
            assert folder and folder.get("success") is not False, folder
            seeded[CollectionNames.RECORDS.value].append(folder["id"])
            made[label] = (kb["id"], folder["id"])

        yield _World(service, org_id, owner, outsider, made["docs"][0], f"kbtext docs {run}", made["other"][1])


def _refusal(result: dict) -> dict:
    return {k: result.get(k) for k in ("success", "code", "reason")}


async def test_an_unknown_knowledge_base_is_not_named(world: _World) -> None:
    w = world

    made = await w.service.create_folder_in_kb(EMAIL, "x", w.owner, w.org_id)
    checked = await w.service.validate_folder_for_upload(EMAIL, w.foreign_folder, w.owner, w.org_id)

    assert _refusal(made) == KB_NOT_FOUND
    assert _refusal(checked) == KB_NOT_FOUND


async def test_a_knowledge_base_the_caller_holds_no_role_on_reads_as_not_found(world: _World) -> None:
    w = world

    made = await w.service.create_folder_in_kb(w.kb_id, "x", w.outsider, w.org_id)
    checked = await w.service.validate_folder_for_upload(w.kb_id, w.foreign_folder, w.outsider, w.org_id)

    assert _refusal(made) == KB_NOT_FOUND
    assert _refusal(checked) == KB_NOT_FOUND


@pytest.mark.parametrize("folder", ["unknown", "foreign"])
async def test_an_upload_folder_that_is_not_in_the_knowledge_base_is_not_named(world: _World, folder: str) -> None:
    w = world
    folder_id = EMAIL if folder == "unknown" else w.foreign_folder

    checked = await w.service.validate_folder_for_upload(w.kb_id, folder_id, w.owner, w.org_id)

    assert _refusal(checked) == {
        "success": False,
        "code": 404,
        "reason": f"The folder was not found in knowledge base '{w.kb_name}' ({w.kb_id}). "
                  "The folder may not exist or may belong to a different knowledge base.",
    }
