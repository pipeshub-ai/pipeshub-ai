"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/agents."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from helper.clients.conversations_client import AgentConversationsClient
from helper.second_user import SecondUser

AGENTS_BASE = "/api/v1/agents"

MISSING_CONVERSATION_ID = "0123456789abcdef01234567"
MALFORMED_CONVERSATION_ID = "not-an-object-id"
MISSING_PROJECT_ID = "abcdef0123456789abcdef01"
MALFORMED_PROJECT_ID = "not-an-object-id"

# The conversation routes only match agentKey against the stored session; none
# of them looks the agent up, so seeded sessions need no real agent behind them.
SEED_AGENT_KEY = "spec-audit-agent"
OTHER_AGENT_KEY = "spec-audit-other-agent"
# Decodes to "bad%key", which guardPathParams refuses before validation runs.
UNSAFE_PATH_SEGMENT = "bad%25key"

WEB_SEARCH_PROVIDERS = ("duckduckgo", "serper", "tavily", "exa")
UNKNOWN_WEB_SEARCH_PROVIDER = "spec-audit-no-such-provider"
UNKNOWN_MODEL_KEY = "spec-audit-no-such-model"

PROJECT_VISIBILITIES = ("private", "project")

# chat.session.schema.ts pins the collection name; plain and agent chats share it.
CHAT_SESSIONS_COLLECTION = "chatSessions"

SeedAgentConversation = Callable[..., str]
SeedProject = Callable[..., str]


def new_run_id() -> str:
    """A well-formed runId that no in-flight run is registered under."""
    return str(uuid.uuid4())


class AgentsAuditClient(AgentConversationsClient):
    """Client for /api/v1/agents, acting as the shared org admin.

    ``set_project`` and ``set_project_visibility`` are inherited; like every method
    here they take ``auth=False`` for an unauthenticated call. Use ``put``/``patch``
    with ``json=`` directly to send a body those helpers cannot express.
    """

    def cancel(
        self,
        agent_key: str,
        conversation_id: str,
        body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /:agentKey/conversations/:conversationId/cancel; body defaults to a fresh runId."""
        payload = {"runId": new_run_id()} if body is None else body
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/cancel",
            json=payload,
            **kwargs,
        )

    def web_search_usage(self, provider: str, **kwargs: Any) -> requests.Response:
        return self.get(f"/web-search-usage/{provider}", **kwargs)

    def model_usage(self, model_key: str, **kwargs: Any) -> requests.Response:
        return self.get(f"/model-usage/{model_key}", **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an agents route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{AGENTS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
