"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/conversations."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from typing import Any, Callable

import requests
from bson import ObjectId
from pymongo import MongoClient

from helper.agui_sse import (
    AGUI,
    conversation_created_value,
    is_conversation_created,
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_error_message,
    run_finished_result,
)
from helper.clients.conversations_client import ConversationsClient
from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.second_user import SecondUser
from helper.openapi_search_validator import assert_matches_component_schema

CONVERSATIONS_BASE = "/api/v1/conversations"

MISSING_CONVERSATION_ID = "0123456789abcdef01234567"
MALFORMED_CONVERSATION_ID = "not-an-object-id"

# Attachment record ids are graph keys (uuid4), not Mongo ObjectIds.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
# Decodes to "bad%id", which guardPathParams refuses before validation runs.
UNSAFE_RECORD_ID = "bad%25id"
OVERLONG_RECORD_ID = "a" * 257

UNKNOWN_RUN_ID = "11111111-1111-4111-8111-111111111111"
MALFORMED_RUN_ID = "not-a-uuid"

PROJECT_VISIBILITIES = ("private", "project")

# Set explicitly in chat.session.schema.ts; holds chat and agent sessions alike.
COLLECTION = "chatSessions"
MESSAGES_COLLECTION = "chatSessionMessages"

CREATE_ROUTE = f"{CONVERSATIONS_BASE}/create"
STREAM_ROUTE = f"{CONVERSATIONS_BASE}/stream"
LIST_ROUTE = CONVERSATIONS_BASE
BY_ID_ROUTE = f"{CONVERSATIONS_BASE}/:conversationId"
MESSAGES_ROUTE = f"{CONVERSATIONS_BASE}/:conversationId/messages"
MESSAGES_STREAM_ROUTE = f"{CONVERSATIONS_BASE}/:conversationId/messages/stream"
UPLOAD_ROUTE = f"{CONVERSATIONS_BASE}/attachments/upload"
DELETE_ATTACHMENT_ROUTE = f"{CONVERSATIONS_BASE}/attachments/:recordId"
SHARE_ROUTE = f"{BY_ID_ROUTE}/share"
UNSHARE_ROUTE = f"{BY_ID_ROUTE}/unshare"
PROJECT_ROUTE = f"{BY_ID_ROUTE}/project"
PROJECT_VISIBILITY_ROUTE = f"{BY_ID_ROUTE}/project-visibility"
REGENERATE_ROUTE = f"{BY_ID_ROUTE}/message/:messageId/regenerate"
CANCEL_ROUTE = f"{BY_ID_ROUTE}/cancel"
TITLE_ROUTE = f"{BY_ID_ROUTE}/title"
FEEDBACK_ROUTE = f"{BY_ID_ROUTE}/message/:messageId/feedback"
ARCHIVE_ROUTE = f"{BY_ID_ROUTE}/archive"
UNARCHIVE_ROUTE = f"{BY_ID_ROUTE}/unarchive"
ARCHIVES_ROUTE = f"{CONVERSATIONS_BASE}/show/archives"
ARCHIVES_SEARCH_ROUTE = f"{CONVERSATIONS_BASE}/show/archives/search"

JSON_HEADERS = {"Content-Type": "application/json"}
MALFORMED_JSON_BODY = "{not json"
INVALID_AUTH_HEADERS = {"Authorization": "Bearer not-a-jwt"}

# A token of the suite's own OAuth client without any conversation:* scope.
NARROW_SCOPE = "org:read"

# Short questions with short answers keep each real LLM turn cheap.
CHEAP_QUERY = "Reply with only the word pong."
CHEAP_FOLLOW_UP = "Reply with only the word ping."
LLM_TIMEOUT_SECONDS = 600

UNIVERSAL_CHAT_MODES = ("agent", "internal_search", "web_search")
# Accepted by the non-streaming routes and run as internal search.
LEGACY_CHAT_MODES = ("deep", "quick", "verification", "auto")
REASONING_EFFORTS = ("none", "low", "medium", "high", "max")

SOME_UUID = "5f1c7c4e-2b4a-4b8e-9a51-3d6f0c2a7e11"

APPLIED_NODE = {"id": SOME_UUID, "name": "Spec audit", "nodeType": "app", "connector": "KB"}

MultipartFiles = list[tuple[str, tuple[str, io.BytesIO, str]]]
SeedConversation = Callable[..., str]
SeedTurn = Callable[..., tuple[str, str, str]]
SeedProject = Callable[[], str]
UploadAttachment = Callable[..., requests.Response]


class ConversationsAuditClient(ConversationsClient):
    """ConversationsClient plus the routes it has no method for, as the shared org admin."""

    def upload_attachments(
        self,
        files: MultipartFiles | None,
        *,
        conversation_id: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /attachments/upload as multipart; ``files=None`` sends no file part."""
        data = {} if conversation_id is None else {"conversationId": conversation_id}
        if files is None:
            # requests only emits multipart when a file part is present.
            return self.post(
                "/attachments/upload",
                auth=auth,
                files={"conversationId": (None, conversation_id or "")},
                **kwargs,
            )
        return self.post(
            "/attachments/upload", auth=auth, files=files, data=data, **kwargs
        )

    def delete_attachment(
        self, record_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.delete(f"/attachments/{record_id}", auth=auth, **kwargs)

    def cancel_stream(
        self,
        conversation_id: str,
        run_id: str = UNKNOWN_RUN_ID,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        kwargs.setdefault("json", {"runId": run_id})
        return self.post(f"/{conversation_id}/cancel", auth=auth, **kwargs)


def share_entry(user_id: str, access_level: str = "read") -> dict[str, Any]:
    """One ``sharedWith`` element as Mongoose stores it, for seeding."""
    return {"userId": ObjectId(user_id), "accessLevel": access_level, "_id": ObjectId()}


def error_of(resp: requests.Response, status: int) -> dict[str, Any]:
    """The ``error`` object of an error response, after checking its status."""
    assert resp.status_code == status, resp.text[:500]
    error: dict[str, Any] = resp.json()["error"]
    return error


def attachment_files(
    name: str = "spec-audit.txt",
    content: bytes = b"Seeded by the conversations spec audit.\n",
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


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a conversations route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = {"Authorization": f"Bearer {user.token}"}
    # With files, requests must set the multipart boundary itself.
    if "files" not in kwargs:
        headers["Content-Type"] = "application/json"
    return requests.request(
        method,
        f"{user.base_url}{CONVERSATIONS_BASE}{path}",
        headers=headers,
        **kwargs,
    )


def _without(node: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in node.items() if k != key}


# Bodies every chat turn route (create, stream, messages, messages/stream) refuses in its
# validator: (fields merged over a valid body, or None to drop a key; field the 400 names).
INVALID_TURN_FIELDS: list[tuple[str, dict[str, Any], str]] = [
    ("missing-query", {"query": None}, "body.query"),
    ("empty-query", {"query": ""}, "body.query"),
    ("query-over-100000-chars", {"query": "a" * 100_001}, "body.query"),
    ("query-not-a-string", {"query": 42}, "body.query"),
    *[
        (f"applied-app-without-{key}", {"appliedFilters": {"apps": [_without(APPLIED_NODE, key)]}}, f"body.appliedFilters.apps.0.{key}")
        for key in ("id", "name", "nodeType", "connector")
    ],
    *[
        (f"applied-kb-without-{key}", {"appliedFilters": {"kb": [_without(APPLIED_NODE, key)]}}, f"body.appliedFilters.kb.0.{key}")
        for key in ("id", "name", "nodeType", "connector")
    ],
    ("filter-app-not-a-uuid", {"filters": {"apps": ["not-a-uuid"]}}, "body.filters.apps.0"),
    ("filter-kb-not-a-uuid", {"filters": {"kb": ["not-a-uuid"]}}, "body.filters.kb.0"),
    ("attachment-without-record-id", {"attachments": [{"recordName": "x.txt"}]}, "body.attachments.0.recordId"),
    ("attachment-empty-record-id", {"attachments": [{"recordId": ""}]}, "body.attachments.0.recordId"),
    ("attachment-unknown-source", {"attachments": [{"recordId": SOME_UUID, "source": "clipboard"}]}, "body.attachments.0.source"),
    ("unknown-reasoning-effort", {"reasoningEffort": "extreme"}, "body.reasoningEffort"),
    ("empty-model-key", {"modelKey": ""}, "body.modelKey"),
    ("empty-model-name", {"modelName": ""}, "body.modelName"),
    ("empty-model-friendly-name", {"modelFriendlyName": ""}, "body.modelFriendlyName"),
    ("empty-timezone", {"timezone": ""}, "body.timezone"),
    ("current-time-not-iso", {"currentTime": "yesterday"}, "body.currentTime"),
    ("current-time-without-zone", {"currentTime": "2026-04-12T16:00:00"}, "body.currentTime"),
    ("empty-tool-name", {"tools": [""]}, "body.tools.0"),
    ("protocol-not-agui", {"protocol": "sse"}, "body.protocol"),
    ("agent-capability-not-boolean", {"agentCapabilities": {"webSearch": "yes"}}, "body.agentCapabilities.webSearch"),
    ("run-id-not-a-uuid", {"runId": "not-a-uuid"}, "body.runId"),
    ("chat-mode-agent-with-sub-mode", {"chatMode": "agent:quick"}, "body.chatMode"),
    ("unknown-chat-mode", {"chatMode": "turbo"}, "body.chatMode"),
]


def turn_body(base: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """``base`` with ``fields`` merged in; a ``None`` value removes that key."""
    body = copy.deepcopy(base)
    for key, value in fields.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def validation_fields(resp: requests.Response) -> list[str]:
    """The fields a Node ``VALIDATION_ERROR`` names; fails on any other body."""
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    return [str(e["field"]) for e in error["metadata"]["errors"]]


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


class StreamRun:
    """A conversation stream read to its end; every frame checked against ``event_schema``."""

    def __init__(self, resp: requests.Response, event_schema: str, *, status: int = 200) -> None:
        assert resp.status_code == status, resp.text[:500]
        assert resp.headers["Content-Type"].startswith("text/event-stream"), resp.headers
        self.events: list[tuple[str, Any]] = []
        self.created: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.error_code: str | None = None
        for envelope in iter_sse_envelopes(resp):
            payload = json.loads(envelope["data"])
            assert_matches_component_schema({"event": envelope["event"], "data": payload}, event_schema)
            self.events.append((envelope["event"], payload))
            if envelope["event"] == AGUI.CUSTOM and is_conversation_created(payload):
                self.created = conversation_created_value(payload)
            elif is_root_finished(envelope["event"], payload):
                self.result = run_finished_result(payload)
            elif is_root_error(envelope["event"], payload):
                self.error = run_error_message(payload)
                self.error_code = payload.get("code")

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]
