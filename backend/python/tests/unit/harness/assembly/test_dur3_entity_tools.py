"""DUR3-B2: the knowledge-graph entity tools reach a durable run exactly when they reach a legacy request.

Legacy offers ``search_entities`` (and, progressively, ``find_records_by_entity``) when the query service's entity
store has its collection (main #3308/#3770, ``load_entity_vector_store``). The durable worker built no entity store,
so a durable run never had them. Both drivers are shown one task with and without a store.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import drive_captured, first_difference
from tests.harness_replay.world import Doc, WorldFixtures

SEARCH_ENTITIES = "knowledgegraph__search_entities"
FIND_BY_ENTITY = "knowledgegraph__find_records_by_entity"


def _store() -> Any:  # noqa: ANN401 - an EntityVectorStore double
    store = MagicMock()
    store.collection_exists = AsyncMock(return_value=True)
    return store


def _spec(store: Any) -> DriveSpec:  # noqa: ANN401
    return DriveSpec(
        scenario="dur3-entity-tools",
        query="Who works on the Atlas project?",
        fixtures=WorldFixtures(knowledge=[Doc(id="atlas", title="Atlas", text="Priya leads Atlas.")]),
        knowledge_search=True,
        fake_toolsets=(),
        entity_store=store,
    )


@pytest.mark.parametrize("with_store", [True, False])
async def test_dur3_b2_entity_tools_on_both_loops_exactly_when_a_store_is_wired(with_store: bool) -> None:
    script = ModelScript(id="entity", turns=[Turn(reply=Reply(text="Priya leads Atlas."))])
    legacy = await drive_captured(legacy_driver.drive, _spec(_store() if with_store else None), script)
    durable = await drive_captured(durable_driver.drive, _spec(_store() if with_store else None), script)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    names = durable.requests[0].tool_names
    assert (SEARCH_ENTITIES in names) is with_store
    # Granted from the start with search_entities: the chat's tool list is fixed at its first request (R4).
    assert (FIND_BY_ENTITY in names) is with_store


async def test_dur3_b2_the_worker_container_builds_the_entity_store_as_the_query_service_does() -> None:
    from app.harness.assembly.services import ContainerServices, HarnessServices

    assert "entity_vector_store" in HarnessServices.providers  # pyright: ignore[reportUnknownMemberType]
    container = MagicMock()
    container.entity_vector_store = MagicMock(return_value=_store())
    assert await ContainerServices(container).entity_vector_store() is container.entity_vector_store.return_value
    container.entity_vector_store = MagicMock(return_value=MagicMock(collection_exists=AsyncMock(return_value=False)))
    assert await ContainerServices(container).entity_vector_store() is None, "no collection yet: no entity tools"


@pytest.mark.parametrize("switched_on", [True, False])
async def test_sync3_search_by_entities_on_both_loops_exactly_when_the_deployment_switch_offers_it(
    switched_on: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Main's ``ENTITY_SEARCH_TOOL`` (KnowledgeGraph.tools_for_request): the factory decides it per request; the
    durable toolset source makes the same decision before it loads, so both loops offer the same tools."""
    from unittest.mock import patch

    from app.agents.agent_loop.entity_search_tool import (
        ENTITY_SEARCH_TOOL_ENV_VAR,
        SEARCH_BY_ENTITIES_TOOL_NAME,
    )

    monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true" if switched_on else "false")
    script = ModelScript(id="entity", turns=[Turn(reply=Reply(text="Priya leads Atlas."))])
    flag_on = AsyncMock(return_value=True)
    # Both bindings of the flag: the factory imports it by name (main), the durable toolset source from its module.
    with patch("app.services.featureflag.platform_settings.is_named_entity_extraction_enabled", flag_on), patch(
        "app.agents.agent_loop.factory.is_named_entity_extraction_enabled", flag_on
    ):
        legacy = await drive_captured(legacy_driver.drive, _spec(_store()), script)
        durable = await drive_captured(durable_driver.drive, _spec(_store()), script)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert (SEARCH_BY_ENTITIES_TOOL_NAME in durable.requests[0].tool_names) is switched_on
