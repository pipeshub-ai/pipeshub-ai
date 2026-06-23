"""Conversations API clients for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


def _request_options(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split HTTP options from payload/query kwargs."""
    options: dict[str, Any] = {}
    for key in ("auth", "headers", "timeout", "params", "stream"):
        if key in kwargs:
            options[key] = kwargs.pop(key)
    if "auth" not in options:
        options["auth"] = True
    return options, kwargs


def _sse_headers(options: dict[str, Any], *, stream: bool) -> None:
    if not stream:
        return
    headers = dict(options.get("headers") or {})
    headers.setdefault("Accept", "text/event-stream")
    options["headers"] = headers
    options["stream"] = True


def _merge_json_body(
    *,
    query: str | None,
    json: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = dict(json) if json is not None else {}
    if query is not None:
        body.setdefault("query", query)
    body.update(payload)
    return body


class ConversationsClient(APIClient):
    """Client for /api/v1/conversations endpoints (non-agent chat)."""

    BASE = "/api/v1/conversations"

    def list_conversations(self, **kwargs: Any) -> requests.Response:
        """List conversations (GET /)."""
        return self._list_get("/", **kwargs)

    def get_conversation(self, conversation_id: str, **kwargs: Any) -> requests.Response:
        """Get conversation by id."""
        options, params = _request_options(kwargs)
        if params:
            options["params"] = params
        return self.get(f"/{conversation_id}", **options)

    def delete_conversation(self, conversation_id: str, **kwargs: Any) -> requests.Response:
        """Delete a conversation."""
        return self.delete(f"/{conversation_id}", **kwargs)

    def stream_conversation(
        self,
        *,
        query: str | None = None,
        json: dict[str, Any] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Start conversation stream (POST /stream)."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        return self.post(
            "/stream",
            json=_merge_json_body(query=query, json=json, payload=payload),
            **options,
        )

    def stream_message(
        self,
        conversation_id: str,
        *,
        query: str | None = None,
        json: dict[str, Any] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Add message via SSE stream."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        return self.post(
            f"/{conversation_id}/messages/stream",
            json=_merge_json_body(query=query, json=json, payload=payload),
            **options,
        )

    def archive_conversation(self, conversation_id: str, **kwargs: Any) -> requests.Response:
        """Archive a conversation (PATCH /{id}/archive)."""
        return self.patch(f"/{conversation_id}/archive", **kwargs)

    def unarchive_conversation(self, conversation_id: str, **kwargs: Any) -> requests.Response:
        """Unarchive a conversation (PATCH /{id}/unarchive)."""
        return self.patch(f"/{conversation_id}/unarchive", **kwargs)

    def update_title(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Update conversation title (PATCH /{id}/title)."""
        options, payload = _request_options(kwargs)
        body = dict(json) if json is not None else {}
        if title is not None:
            body.setdefault("title", title)
        body.update(payload)
        return self.patch(f"/{conversation_id}/title", json=body, **options)

    def share_conversation(
        self,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Share a conversation (POST /{id}/share)."""
        options, payload = _request_options(kwargs)
        return self.post(f"/{conversation_id}/share", json=payload, **options)

    def unshare_conversation(
        self,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Unshare a conversation (POST /{id}/unshare)."""
        options, payload = _request_options(kwargs)
        return self.post(f"/{conversation_id}/unshare", json=payload, **options)

    def list_archived_conversations(self, **kwargs: Any) -> requests.Response:
        """List archived conversations (GET /show/archives)."""
        return self._list_get("/show/archives", **kwargs)

    def search_archived_conversations(self, **kwargs: Any) -> requests.Response:
        """Search archived conversations (GET /show/archives/search)."""
        return self._list_get("/show/archives/search", **kwargs)

    def regenerate_message(
        self,
        conversation_id: str,
        message_id: str,
        *,
        json: dict[str, Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        """Regenerate a message (POST /{id}/message/{messageId}/regenerate)."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        body = dict(json) if json is not None else {}
        body.update(payload)
        return self.post(
            f"/{conversation_id}/message/{message_id}/regenerate",
            json=body,
            **options,
        )

    def submit_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Submit feedback for a message."""
        options, payload = _request_options(kwargs)
        return self.post(
            f"/{conversation_id}/message/{message_id}/feedback",
            json=payload,
            **options,
        )

    def _list_get(self, path: str, **kwargs: Any) -> requests.Response:
        options, params = _request_options(kwargs)
        if params:
            options["params"] = params
        return self.get(path, **options)


class AgentConversationsClient(APIClient):
    """Client for /api/v1/agents/{agentKey}/conversations/* endpoints."""

    BASE = "/api/v1/agents"

    def list_conversations(self, agent_key: str, **kwargs: Any) -> requests.Response:
        """List agent conversations (GET /{agentKey}/conversations)."""
        return self._list_get(f"/{agent_key}/conversations", **kwargs)

    def list_grouped_archives(self, **kwargs: Any) -> requests.Response:
        """List grouped archived conversations (GET /conversations/show/archives)."""
        return self._list_get("/conversations/show/archives", **kwargs)

    def list_archived_conversations(self, agent_key: str, **kwargs: Any) -> requests.Response:
        """List archived conversations for an agent."""
        return self._list_get(f"/{agent_key}/conversations/show/archives", **kwargs)

    def get_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Get conversation by id."""
        options, params = _request_options(kwargs)
        if params:
            options["params"] = params
        return self.get(f"/{agent_key}/conversations/{conversation_id}", **options)

    def delete_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Delete a conversation."""
        return self.delete(f"/{agent_key}/conversations/{conversation_id}", **kwargs)

    def archive_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Archive a conversation."""
        return self.post(f"/{agent_key}/conversations/{conversation_id}/archive", **kwargs)

    def unarchive_conversation(
        self,
        agent_key: str,
        conversation_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Unarchive a conversation."""
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/unarchive",
            **kwargs,
        )

    def update_title(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        title: str | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Update conversation title (PATCH /{id}/title)."""
        options, payload = _request_options(kwargs)
        body = dict(json) if json is not None else {}
        if title is not None:
            body.setdefault("title", title)
        body.update(payload)
        return self.patch(
            f"/{agent_key}/conversations/{conversation_id}/title",
            json=body,
            **options,
        )

    def stream_conversation(
        self,
        agent_key: str,
        *,
        query: str | None = None,
        json: dict[str, Any] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Start agent conversation stream (POST /{agentKey}/conversations/stream)."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        return self.post(
            f"/{agent_key}/conversations/stream",
            json=_merge_json_body(query=query, json=json, payload=payload),
            **options,
        )

    def stream_message(
        self,
        agent_key: str,
        conversation_id: str,
        *,
        query: str | None = None,
        json: dict[str, Any] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Add message via SSE stream."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/messages/stream",
            json=_merge_json_body(query=query, json=json, payload=payload),
            **options,
        )

    def regenerate_message(
        self,
        agent_key: str,
        conversation_id: str,
        message_id: str,
        *,
        json: dict[str, Any] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Regenerate a message (POST .../message/{messageId}/regenerate)."""
        options, payload = _request_options(kwargs)
        _sse_headers(options, stream=stream)
        body = dict(json) if json is not None else {}
        body.update(payload)
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/message/{message_id}/regenerate",
            json=body,
            **options,
        )

    def submit_message_feedback(
        self,
        agent_key: str,
        conversation_id: str,
        message_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Submit feedback for a message."""
        options, payload = _request_options(kwargs)
        return self.post(
            f"/{agent_key}/conversations/{conversation_id}/message/{message_id}/feedback",
            json=payload,
            **options,
        )

    def upload_attachments(
        self,
        agent_key: str,
        *,
        files: Any,
        **kwargs: Any,
    ) -> requests.Response:
        """Upload chat attachments (multipart)."""
        return self.post(
            f"/{agent_key}/conversations/attachments/upload",
            files=files,
            **kwargs,
        )

    def delete_attachment(
        self,
        agent_key: str,
        record_id: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Delete an uploaded attachment by record id."""
        return self.delete(
            f"/{agent_key}/conversations/attachments/{record_id}",
            **kwargs,
        )

    def _list_get(self, path: str, **kwargs: Any) -> requests.Response:
        options, params = _request_options(kwargs)
        if params:
            options["params"] = params
        return self.get(path, **options)
