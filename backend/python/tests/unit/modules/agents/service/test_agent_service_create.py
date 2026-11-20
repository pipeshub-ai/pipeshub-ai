"""`AgentService.create` without edition extras: the agent document carries no
fields beyond the shared schema, and a lost unique-constraint race is not an HTTP error."""

import logging

from fastapi import HTTPException

from app.modules.agents.service.agent_service import AgentService
from app.modules.agents.service.models import AgentActor, AgentSpec
from app.services.graph_db.errors import UniqueConstraintViolation
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    user_key,
)

ALICE = AgentActor(user_key=user_key("alice"), user_id="u-alice", org_id="org-1")


async def test_new_agent_carries_its_org_and_no_edition_fields() -> None:
    graph = InMemoryGraph()

    created = await AgentService(graph, FakeConfigService(), logging.getLogger("test")).create(  # type: ignore[arg-type]
        ALICE, AgentSpec.model_validate({"name": "Offer drafter", "handle": "ignored"}),
    )

    stored = graph.nodes[AGENTS][created.agent_key]
    assert stored["orgId"] == "org-1"
    assert not {"handle", "createdVia", "sourceConversationId", "sourceMessageId"} & stored.keys()


def test_unique_violation_is_not_an_http_error() -> None:
    assert not issubclass(UniqueConstraintViolation, HTTPException)
