"""Node-backed IConversationWriter adapter.

Mints a scoped JWT (using the same `scopedJwtSecret` pattern already used by
`modules/transformers/blob_storage.py:64-68`) and POSTs to the Node internal
route `POST /api/v1/workflows/internal/conversations/{id}/messages`.

Tolerates 404 silently (conversation deleted before run finished).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from app.services.workflows.domain.errors import ConversationWriteError
from app.services.workflows.domain.models import RunResultMessage

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

__all__ = ["NodeConversationWriter", "build_node_conversation_writer"]

logger = logging.getLogger(__name__)


async def build_node_conversation_writer(
    config_service: "ConfigurationService",
    *,
    client: "httpx.AsyncClient | None" = None,
) -> "NodeConversationWriter | None":
    """Resolve the Node endpoint and scoped-JWT secret from config.

    Returns None when no `scopedJwtSecret` is configured -- every caller
    treats conversation write-back as best-effort, so a missing secret
    degrades that one feature instead of failing startup.
    """
    from app.config.constants.service import DefaultEndpoints

    try:
        secret_keys = await config_service.get_config("/services/secretKeys")
    except Exception:
        logger.warning("conversation_writer: could not read secret keys", exc_info=True)
        return None
    secret = (secret_keys or {}).get("scopedJwtSecret")
    if not secret:
        return None

    try:
        endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
        node_base_url = (
            (endpoints or {}).get("nodejs", {}).get("endpoint")
            if isinstance(endpoints, dict)
            else None
        ) or DefaultEndpoints.NODEJS_ENDPOINT.value
    except Exception:
        node_base_url = DefaultEndpoints.NODEJS_ENDPOINT.value

    return NodeConversationWriter(
        node_base_url=node_base_url, scoped_jwt_secret=secret, client=client,
    )


class NodeConversationWriter:
    def __init__(
        self,
        *,
        node_base_url: str,
        scoped_jwt_secret: str,
        logger: "Logger | None" = None,
        client: "httpx.AsyncClient | None" = None,
    ) -> None:
        self._base_url = node_base_url.rstrip("/")
        self._secret = scoped_jwt_secret
        self._logger = logger or logging.getLogger(__name__)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def aclose(self) -> None:
        """Close the underlying HTTP client, if this instance owns it."""
        if self._owns_client:
            await self._client.aclose()

    def _mint_token(self, org_id: str) -> str:
        """Mint a short-lived internal token. Same approach as blob_storage.py."""
        import time

        import jwt

        now = int(time.time())
        payload = {
            "iss": "pipeshub-python",
            "aud": "pipeshub-node-internal",
            "iat": now,
            "exp": now + 120,
            "org_id": org_id,
            "scopes": ["conversation:create"],
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    async def write(
        self,
        *,
        run_id: str,
        org_id: str,
        content: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
        kind: str = "text",
    ) -> None:
        """Emit a streaming message from within the running workflow back to
        the originating conversation (backs `ctx.emit()`).  When
        `conversation_id` is None the call is a no-op (workflow was not
        started from chat)."""
        if not conversation_id:
            self._logger.debug("conversation_writer.write: no conversation_id, skipping (run_id=%s)", run_id)
            return
        token = self._mint_token(org_id)
        # Deliberately not `/messages`: that route records a terminal run
        # result and closes the conversation out. A mid-run emit does neither.
        url = (
            f"{self._base_url}/api/v1/workflows/internal/conversations"
            f"/{conversation_id}/emit"
        )
        payload = {
            "runId": run_id,
            "content": content,
            "kind": kind,
        }
        if user_id:
            payload["userId"] = user_id
        try:
            response = await self._client.post(
                url, json=payload, headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            self._logger.warning("conversation_writer.write: request failed: %s", exc)
            return
        if response.status_code == 404:
            self._logger.debug("conversation_writer.write: conversation %s not found (deleted?)", conversation_id)
            return
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._logger.warning("conversation_writer.write: HTTP error %s", exc)

    async def append_result(
        self, conversation_id: str, org_id: str, msg: RunResultMessage
    ) -> None:
        token = self._mint_token(org_id)
        url = f"{self._base_url}/api/v1/workflows/internal/conversations/{conversation_id}/messages"
        error_text = msg.error
        if error_text:
            error_text = error_text.replace("<", "&lt;").replace(">", "&gt;")
        payload = {
            "workflowId": msg.workflow_id,
            "runId": msg.run_id,
            "status": msg.status,
            "outputSummary": msg.output_summary,
            "redirectLink": msg.redirect_link,
            "workflowName": msg.workflow_name,
            "error": error_text,
            "isDryRun": msg.is_dry_run,
            "triggerKind": msg.trigger_kind,
            "startedAt": msg.started_at,
            "completedAt": msg.completed_at,
            "suspensionKind": msg.suspension_kind,
        }
        try:
            response = await self._client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            self._logger.error("conversation_writer: request failed: %s", exc)
            raise ConversationWriteError(str(exc)) from exc

        if response.status_code == 404:
            self._logger.warning(
                "conversation_writer: conversation %s not found (deleted?), skipping",
                conversation_id,
            )
            return

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._logger.error("conversation_writer: HTTP error %s", exc)
            raise ConversationWriteError(str(exc)) from exc

    async def link_workflow(self, conversation_id: str, org_id: str, workflow_id: str) -> None:
        """Register a workflow as connected to a conversation (best-effort)."""
        await self._patch_workflow_link(conversation_id, org_id, workflow_id, action="add")

    async def unlink_workflow(self, conversation_id: str, org_id: str, workflow_id: str) -> None:
        """Remove a workflow's connection to a conversation (best-effort)."""
        await self._patch_workflow_link(conversation_id, org_id, workflow_id, action="remove")

    async def list_linked_workflows(self, conversation_id: str, org_id: str) -> list[str]:
        """Workflow ids explicitly linked to a conversation.

        A workflow can be attached to a conversation it was not created from,
        and that link lives only in Mongo (Node owns conversations), so the
        by-conversation listing cannot be answered from the task store alone.
        Returns an empty list on any failure: the caller unions this with the
        created-from set, and a transient Node outage should degrade to "the
        ones we know about" rather than fail the whole panel.
        """
        token = self._mint_token(org_id)
        url = f"{self._base_url}/api/v1/workflows/internal/conversations/{conversation_id}/workflows"
        try:
            response = await self._client.get(
                url, headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._logger.warning(
                "conversation_writer.list_linked_workflows: request failed for %s: %s",
                conversation_id, exc,
            )
            return []
        try:
            ids = response.json().get("workflowIds") or []
        except ValueError:
            self._logger.warning(
                "conversation_writer.list_linked_workflows: non-JSON response for %s",
                conversation_id,
            )
            return []
        return [str(i) for i in ids if i]

    async def _patch_workflow_link(
        self, conversation_id: str, org_id: str, workflow_id: str, *, action: str
    ) -> None:
        token = self._mint_token(org_id)
        url = f"{self._base_url}/api/v1/workflows/internal/conversations/{conversation_id}/workflows"
        try:
            response = await self._client.patch(
                url,
                json={"action": action, "workflowId": workflow_id},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            self._logger.warning("conversation_writer._patch_workflow_link: request failed: %s", exc)
            return
        if response.status_code == 404:
            self._logger.debug(
                "conversation_writer._patch_workflow_link: conversation %s not found", conversation_id
            )
            return
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._logger.warning("conversation_writer._patch_workflow_link: HTTP error %s", exc)
