"""Agent attachment upload integration tests.

`POST /api/v1/agents/{agentKey}/conversations/attachments/upload`

Uploads the shared Asana DR PDF fixture used by KB indexing tests, then
validates the live JSON response against the OpenAPI operation contract.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import AgentConversationsClient
from openapi_schema_validator import assert_response_matches_openapi_operation


def _response_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise AssertionError(
            f"Expected JSON response, got status={resp.status_code}: {resp.text[:500]}"
        ) from exc
    assert isinstance(data, dict), f"Expected dict JSON body, got: {data!r}"
    return data


def _build_agent_payload(reasoning_multimodal_llm_model: Any) -> dict[str, Any]:
    return {
        "name": f"it-agent-attachments-{uuid4().hex[:8]}",
        "models": [
            {
                "modelKey": reasoning_multimodal_llm_model.model_key,
                "modelName": reasoning_multimodal_llm_model.model_name,
                "provider": reasoning_multimodal_llm_model.provider,
                "isReasoning": True,
            },
        ],
    }


def _pdf_files(asana_pdf_blob: dict[str, Any]) -> list[tuple[str, tuple[str, io.BytesIO, str]]]:
    return [
        (
            "files",
            (
                asana_pdf_blob["originalname"],
                io.BytesIO(asana_pdf_blob["buffer"]),
                asana_pdf_blob["mimetype"],
            ),
        ),
    ]


class AgentAttachmentTestBase:
    """Shared agents + agent conversations clients."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        agents_client: AgentsClient,
        agent_conversations_client: AgentConversationsClient,
    ) -> None:
        self.agents = agents_client
        self.conversations = agent_conversations_client
        self.timeout = agents_client._client.timeout_seconds

    @pytest.fixture
    def created_agent_key(
        self,
        reasoning_multimodal_llm_model: Any,
    ) -> Iterator[str]:
        resp = self.agents.create_agent(
            **_build_agent_payload(reasoning_multimodal_llm_model),
        )
        assert resp.status_code == 201, f"{resp.status_code}: {resp.text}"

        body = _response_json(resp)
        agent = body.get("agent")
        assert isinstance(agent, dict), f"Expected response.agent object, got: {body!r}"
        agent_key = agent.get("_key")
        assert isinstance(agent_key, str) and agent_key, (
            f"Expected response.agent._key, got: {body!r}"
        )

        try:
            yield agent_key
        finally:
            try:
                self.agents.delete_agent(agent_key)
            except Exception:
                pass


@pytest.mark.integration
class TestAgentAttachmentUpload(AgentAttachmentTestBase):

    def test_upload_pdf_matches_openapi_spec_and_returns_attachment_ref(
        self,
        asana_pdf_blob: dict[str, Any],
        created_agent_key: str,
    ) -> None:
        cleanup_record_ids: list[str] = []

        try:
            resp = self.conversations.upload_attachments(
                created_agent_key,
                files=_pdf_files(asana_pdf_blob),
                timeout=self.timeout,
            )
            assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
            assert "application/json" in (resp.headers.get("Content-Type") or "").lower()

            body = _response_json(resp)
            attachments = body.get("attachments")
            if isinstance(attachments, list):
                for item in attachments:
                    if not isinstance(item, dict):
                        continue
                    record_id = item.get("recordId")
                    if isinstance(record_id, str) and record_id:
                        cleanup_record_ids.append(record_id)

            assert_response_matches_openapi_operation(
                body,
                "uploadAgentConversationChatAttachments",
            )

            assert body.get("conversationId") is None
            assert isinstance(attachments, list) and len(attachments) == 1, (
                f"expected exactly one uploaded attachment, got: {body!r}"
            )

            attachment = attachments[0]
            assert isinstance(attachment, dict), f"attachment was not an object: {attachment!r}"
            assert attachment.get("recordName") == asana_pdf_blob["originalname"]
            assert attachment.get("mimeType") == asana_pdf_blob["mimetype"]
            assert str(attachment.get("extension", "")).lower() == "pdf"
            assert isinstance(attachment.get("recordId"), str) and attachment["recordId"]
            assert (
                isinstance(attachment.get("virtualRecordId"), str)
                and attachment["virtualRecordId"]
            )
            assert isinstance(attachment.get("ocrMode"), str) and attachment["ocrMode"]
        finally:
            for record_id in cleanup_record_ids:
                try:
                    self.conversations.delete_attachment(
                        created_agent_key,
                        record_id,
                        timeout=self.timeout,
                    )
                except Exception:
                    pass

    def test_delete_uploaded_attachment_returns_204_with_empty_body(
        self,
        asana_pdf_blob: dict[str, Any],
        created_agent_key: str,
    ) -> None:
        upload_resp = self.conversations.upload_attachments(
            created_agent_key,
            files=_pdf_files(asana_pdf_blob),
            timeout=self.timeout,
        )
        assert upload_resp.status_code == 200, (
            f"{upload_resp.status_code}: {upload_resp.text}"
        )

        upload_body = _response_json(upload_resp)
        attachments = upload_body.get("attachments")
        assert isinstance(attachments, list) and attachments, (
            f"expected uploaded attachment refs before delete, got: {upload_body!r}"
        )
        attachment = attachments[0]
        assert isinstance(attachment, dict), f"attachment was not an object: {attachment!r}"
        record_id = attachment.get("recordId")
        assert isinstance(record_id, str) and record_id, (
            f"upload response missing attachment recordId: {upload_body!r}"
        )

        delete_resp = self.conversations.delete_attachment(
            created_agent_key,
            record_id,
            timeout=self.timeout,
        )
        assert delete_resp.status_code == 204, (
            f"{delete_resp.status_code}: {delete_resp.text}"
        )
        assert delete_resp.text == ""
