"""Chat routes Node reaches from both its conversation and agent surfaces.

Node checks `conversation:chat` on its conversation routes and `agent:execute`
on its agent conversation routes, then forwards the caller's OAuth token to
the same Python routes. Each Python route has to admit the scope of every
Node route that calls it, or an app granted exactly what Node asks for is
refused on the second hop.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

# app.edition_config and app.api.routes.search import each other; load it first.
import app.edition_config  # noqa: F401
from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.api.middlewares.token_policy import AuthTokenType
from app.api.routes import agent, chatbot

CHAT = "conversation:chat"
AGENT = "agent:execute"


def _oauth_scopes(router, method: str, path: str) -> set[str]:
    route = next(
        r for r in router.routes
        if isinstance(r, APIRoute) and r.path == path and method in (r.methods or ())
    )
    scopes = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        policy = getattr(dependant.call, AUTH_POLICY_ATTR, None)
        if policy is not None:
            scopes |= {getattr(s, "value", s) for s in policy.oauth_scopes}
        pending.extend(dependant.dependencies)
    return scopes


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/chat/attachments/upload"),
        ("DELETE", "/chat/attachments/{record_id}"),
        ("POST", "/chat/cancel"),
    ],
)
def test_shared_chat_routes_admit_both_node_scopes(method, path) -> None:
    assert {CHAT, AGENT} <= _oauth_scopes(chatbot.router, method, path)


@pytest.mark.parametrize("path", ["/{agent_id}/chat", "/{agent_id}/chat/stream"])
def test_agent_chat_routes_admit_conversation_chat(path) -> None:
    # Node's conversation routes run the universal agent here in agent chat mode.
    assert {CHAT, AGENT} <= _oauth_scopes(agent.router, "POST", path)


def _request(user: dict) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user=user))


def _oauth(*scopes: str) -> dict:
    return {"userId": "u-1", "orgId": "org-1", "isOAuth": True, "oauthScopes": list(scopes)}


class TestNamedAgentsNeedAgentExecute:
    async def test_universal_agent_runs_with_conversation_chat(self) -> None:
        await agent.require_agent_execute_for_named_agents(
            _request(_oauth(CHAT)), agent.UNIVERSAL_AGENT_ID,
        )

    async def test_named_agent_refuses_conversation_chat_alone(self) -> None:
        with pytest.raises(HTTPException) as err:
            await agent.require_agent_execute_for_named_agents(_request(_oauth(CHAT)), "agent-1")
        assert err.value.status_code == 403
        assert AGENT in err.value.detail

    async def test_named_agent_runs_with_agent_execute(self) -> None:
        await agent.require_agent_execute_for_named_agents(_request(_oauth(AGENT)), "agent-1")

    async def test_session_token_is_not_scope_checked(self) -> None:
        await agent.require_agent_execute_for_named_agents(
            _request({"userId": "u-1", "orgId": "org-1"}), "agent-1",
        )

    async def test_service_token_is_left_to_the_route_policy(self) -> None:
        user = {"token_type": AuthTokenType.SCOPED.value, "scopes": ["conversation:create"]}
        await agent.require_agent_execute_for_named_agents(_request(user), "agent-1")
