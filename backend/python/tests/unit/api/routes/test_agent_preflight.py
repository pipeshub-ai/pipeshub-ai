"""The universal agent answers what the edition answers before the agent runs (the chat extension's `preflight`).

Node sends an Agent-mode question of the assistant to `/agent/agentIdPlaceholder/chat/stream`, not to `/chat/stream`,
so the stream the edition hands back there (a canned card) must come back from this route as it does from that one, with
no agent built and no model call. A named agent is not the assistant, so its questions never go to the hook.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.agents.chat_extension import ChatExtension

CARD = 'event: RUN_FINISHED\ndata: {"card": true}\n\n'


class _CardExtension(ChatExtension):
    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def preflight(self, chat_query, *, graph_provider, config_service, user_id, org_id, logger):  # noqa: ANN202
        self.asked.append({"query": chat_query.query, "user_id": user_id, "org_id": org_id})

        async def card() -> AsyncIterator[str]:
            yield CARD

        return card()


async def _stream(agent_id: str, extension: ChatExtension) -> tuple[str, MagicMock, MagicMock]:
    from app.api.routes.agent import chat_stream

    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value={"_key": agent_id, "name": "named", "knowledge": [], "toolsets": [], "models": []})
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})
    services = {
        "graph_provider": graph, "retrieval_service": MagicMock(), "reranker_service": MagicMock(),
        "config_service": AsyncMock(), "logger": logging.getLogger("agent-preflight-test"),
    }
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({"query": "@assistant help", "chatMode": "quick"}).encode())
    request.headers = {}
    request.state.user = {"userId": "caller", "orgId": "o1"}
    assistant = AsyncMock(return_value=({"_key": "agentIdPlaceholder", "name": "assistant", "knowledge": [], "toolsets": [], "models": []}, {}))
    loop = MagicMock(side_effect=RuntimeError("the agent loop ran"))
    route_patches = {
        "get_services": {"new_callable": AsyncMock, "return_value": services},
        "_get_user_context": {"return_value": {"userId": "caller", "orgId": "o1"}},
        "_get_user_document": {"new_callable": AsyncMock, "return_value": {"email": "u@x.com", "_key": "caller-key", "isActive": True}},
        "_get_org_info": {"new_callable": AsyncMock, "return_value": {"orgId": "o1", "accountType": "enterprise"}},
        "_assert_agent_belongs_to_caller_org": {"new_callable": AsyncMock},
        "load_entity_vector_store": {"new_callable": AsyncMock, "return_value": None},
        "get_assistant_agent": {"new": assistant},
        "run_agent_loop_stream": {"new": loop},
        "get_chat_extension": {"return_value": extension},
    }
    with ExitStack() as stack:
        for target, kwargs in route_patches.items():
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        # Whatever the installed policy checks of the agent, this test's agent passes it.
        from app.modules.agents.caller_policy import get_agent_caller_policy

        stack.enter_context(patch.object(type(get_agent_caller_policy()), "admit_agent", new_callable=AsyncMock))
        for target in (
            "app.services.featureflag.platform_settings.is_actions_enabled",
            "app.services.featureflag.platform_settings.is_user_context_enabled",
            "app.agents.mcp.service.is_mcp_enabled",
        ):
            stack.enter_context(patch(target, new_callable=AsyncMock, return_value=False))
        response = await chat_stream(request, agent_id)
        chunks = [chunk async for chunk in response.body_iterator]
    return "".join(c.decode() if isinstance(c, bytes) else str(c) for c in chunks), assistant, loop


@pytest.mark.asyncio
class TestTheUniversalAgentAnswersThePreflightStream:
    async def test_the_edition_card_is_the_whole_answer_and_no_agent_is_built(self) -> None:
        extension = _CardExtension()

        body, assistant, loop = await _stream("agentIdPlaceholder", extension)

        assert body == CARD
        assert extension.asked == [{"query": "@assistant help", "user_id": "caller", "org_id": "o1"}]
        assistant.assert_not_called()
        loop.assert_not_called()

    async def test_without_a_preflight_answer_the_assistant_is_built_as_before(self) -> None:
        body, assistant, _loop = await _stream("agentIdPlaceholder", ChatExtension())

        assistant.assert_awaited_once()
        assert CARD not in body

    async def test_a_named_agent_never_asks_the_hook(self) -> None:
        extension = _CardExtension()

        body, _assistant, _loop = await _stream("agent-42", extension)

        assert extension.asked == [] and CARD not in body
