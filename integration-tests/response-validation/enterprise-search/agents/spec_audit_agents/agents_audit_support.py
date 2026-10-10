"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/agents."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from typing import Any, Callable

import pytest
import requests
from pymongo import MongoClient

from helper.agui_sse import iter_sse_envelopes
from helper.clients.conversations_client import AgentConversationsClient
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser

AGENTS_BASE = "/api/v1/agents"

MISSING_CONVERSATION_ID = "0123456789abcdef01234567"
MALFORMED_CONVERSATION_ID = "not-an-object-id"
MISSING_MESSAGE_ID = "76543210fedcba9876543210"
MALFORMED_MESSAGE_ID = "not-a-message-id"
MISSING_PROJECT_ID = "abcdef0123456789abcdef01"
MALFORMED_PROJECT_ID = "not-an-object-id"

# Attachment record ids are graph keys (uuid4), not Mongo ObjectIds.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
OVERLONG_RECORD_ID = "a" * 257

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
# chat.session.message.schema.ts: messages live in their own seq-ordered collection.
CHAT_SESSION_MESSAGES_COLLECTION = "chatSessionMessages"

# A token of the suite's own OAuth client that carries no agent:* scope.
NARROW_SCOPE = "org:read"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# Long enough for a knowledge-free agent's single LLM turn.
ANSWER_TIMEOUT = int(os.getenv("PIPESHUB_TEST_STREAM_TIMEOUT", "0") or 0) or 240

# A string the AI backend keys no agent under.
MISSING_AGENT_KEY = "00000000-0000-4000-8000-0000000000aa"

SeedAgentConversation = Callable[..., str]
MakeAgent = Callable[..., str]
TrackAgent = Callable[[requests.Response], str]
SeedProject = Callable[..., str]
SeedMessage = Callable[..., str]
UploadAttachment = Callable[..., requests.Response]
MultipartFiles = list[tuple[str, tuple[str, io.BytesIO, str]]]


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

    def upload(
        self,
        agent_key: str,
        files: MultipartFiles | None,
        *,
        conversation_id: str | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /:agentKey/conversations/attachments/upload; ``files=None`` sends no file part."""
        if files is None:
            # requests only emits multipart when a file part is present.
            return self.post(
                f"/{agent_key}/conversations/attachments/upload",
                files={"conversationId": (None, conversation_id or "")},
                **kwargs,
            )
        data = {} if conversation_id is None else {"conversationId": conversation_id}
        return self.post(
            f"/{agent_key}/conversations/attachments/upload", files=files, data=data, **kwargs
        )

    def create_agent(self, body: Any, **kwargs: Any) -> requests.Response:
        return self.post("/create", json=body, **kwargs)

    def get_agent(self, agent_key: str, **kwargs: Any) -> requests.Response:
        return self.get(f"/{agent_key}", **kwargs)

    def update_agent(self, agent_key: str, body: Any, **kwargs: Any) -> requests.Response:
        return self.put(f"/{agent_key}", json=body, **kwargs)

    def delete_agent(self, agent_key: str, **kwargs: Any) -> requests.Response:
        return self.delete(f"/{agent_key}", **kwargs)

    def list_agents(self, **kwargs: Any) -> requests.Response:
        """GET /api/v1/agents (no trailing slash); pass ``params=`` for the query."""
        return self.get("", **kwargs)

    def feedback(
        self,
        agent_key: str,
        conversation_id: str,
        message_id: str,
        body: dict[str, Any],
        **kwargs: Any,
    ) -> requests.Response:
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/message/{message_id}/feedback",
            json=body,
            **kwargs,
        )


def unique_agent_name(prefix: str = "spec-audit-agent") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def reasoning_model_entry(model: Any) -> dict[str, Any]:
    """An ``AgentCreateModelEntry`` object for a seeded model, flagged as reasoning."""
    return {
        "modelKey": model.model_key,
        "modelName": model.model_name,
        "provider": model.provider,
        "isReasoning": True,
    }


def attachment_files(
    name: str = "spec-audit.txt",
    content: bytes = b"Seeded by the agents spec audit.\n",
    mimetype: str = "text/plain",
) -> MultipartFiles:
    """One ``files`` part. The default is plain text: parsed in-process, no OCR or LLM."""
    return [("files", (name, io.BytesIO(content), mimetype))]


def uploaded_record_ids(resp: requests.Response) -> list[str]:
    """Record ids from a successful upload response; empty for anything else."""
    if resp.status_code >= 300:
        return []
    try:
        attachments = resp.json().get("attachments") or []
    except (ValueError, AttributeError):
        return []
    return [a["recordId"] for a in attachments if isinstance(a, dict) and a.get("recordId")]


def sse_events(resp: requests.Response) -> list[tuple[str, dict[str, Any]]]:
    """Every frame of a fully read SSE body as ``(event, payload)``."""
    events = []
    for envelope in iter_sse_envelopes(resp):
        try:
            payload = json.loads(envelope["data"])
        except ValueError as exc:
            raise AssertionError(f"SSE frame is not JSON: {envelope!r}") from exc
        events.append((envelope["event"], payload))
    return events


def error_of(resp: requests.Response) -> dict[str, Any]:
    body = resp.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), resp.text[:500]
    return body["error"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def mint_narrow_scope_token(base_url: str, timeout: int = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


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


QUICK_TURN: dict[str, Any] = {"query": "Reply with the single word OK.", "chatMode": "quick"}

# Fields every turn body (create, follow-up, regenerate) shares, each with a value the
# gateway validator refuses.
_CONTEXT_FIELD_REFUSALS: dict[str, dict[str, Any]] = {
    "chat-mode-not-quick": {"chatMode": "agent"},
    "reasoning-effort-unknown": {"reasoningEffort": "extreme"},
    "run-id-not-uuid": {"runId": "not-a-uuid"},
    "current-time-not-iso": {"currentTime": "yesterday"},
    "current-time-without-offset": {"currentTime": "2026-05-19T12:58:01"},
    "filters-kb-not-uuid": {"filters": {"kb": ["not-a-uuid"]}},
    "filters-apps-not-uuid": {"filters": {"apps": ["not-a-uuid"]}},
    "tool-empty": {"tools": [""]},
    "protocol-not-agui": {"protocol": "sse"},
    "capability-not-boolean": {"agentCapabilities": {"webSearch": "yes"}},
    "model-key-empty": {"modelKey": ""},
    "model-name-empty": {"modelName": ""},
    "timezone-empty": {"timezone": ""},
}

_MESSAGE_FIELD_REFUSALS: dict[str, dict[str, Any]] = {
    "empty-query": {"query": ""},
    "overlong-query": {"query": "x" * 100_001},
    "query-not-string": {"query": 42},
    "applied-app-node-incomplete": {"appliedFilters": {"apps": [{"id": "app-1"}]}},
    "applied-kb-node-without-connector": {
        "appliedFilters": {"kb": [{"id": "kb-1", "name": "KB", "nodeType": "kb"}]}
    },
    "attachment-source-unknown": {"attachments": [{"recordId": "r-1", "source": "clipboard"}]},
    "attachment-without-record-id": {"attachments": [{"recordName": "a.txt"}]},
}

_CREATE_FIELD_REFUSALS: dict[str, dict[str, Any]] = {
    "record-id-not-object-id": {"recordIds": ["not-an-object-id"]},
    "project-id-malformed": {"projectId": "not-an-object-id"},
    "project-visibility-unknown": {"projectVisibility": "public"},
}


def refused_bodies(kind: str, base: dict[str, Any]) -> list[Any]:
    """``pytest.param`` bodies the validator refuses: ``base`` with one bad field.

    ``kind`` is ``create``, ``message`` or ``regenerate``; each adds the fields its
    schema has on top of the shared context fields.
    """
    cases = dict(_CONTEXT_FIELD_REFUSALS)
    if kind in ("create", "message"):
        cases.update(_MESSAGE_FIELD_REFUSALS)
        missing_query = {k: v for k, v in base.items() if k != "query"}
        params = [pytest.param(missing_query, id="missing-query")]
    else:
        params = []
    if kind == "create":
        cases.update(_CREATE_FIELD_REFUSALS)
    params += [pytest.param({**base, **override}, id=case) for case, override in cases.items()]
    return params
