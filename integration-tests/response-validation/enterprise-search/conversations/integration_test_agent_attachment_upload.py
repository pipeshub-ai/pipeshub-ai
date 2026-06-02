"""Agent attachment upload integration tests.

`POST /api/v1/agents/{agentKey}/conversations/attachments/upload`

Uploads the shared Asana DR PDF fixture used by KB indexing tests, then
validates the live JSON response against the OpenAPI operation contract.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_HELPER = _ROOT / "helper"
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _HELPER, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from openapi_schema_validator import assert_response_matches_openapi_operation
from pipeshub_client import PipeshubClient


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


@pytest.mark.integration
class TestAgentAttachmentUpload:

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.headers = pipeshub_client.auth_headers
        self.timeout = pipeshub_client.timeout_seconds

    @pytest.fixture
    def created_agent_key(
        self,
        pipeshub_client: PipeshubClient,
        reasoning_multimodal_llm_model: Any,
    ):
        resp = requests.post(
            f"{pipeshub_client.base_url}/api/v1/agents/create",
            headers=pipeshub_client.auth_headers,
            json=_build_agent_payload(reasoning_multimodal_llm_model),
            timeout=pipeshub_client.timeout_seconds,
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
                requests.delete(
                    f"{pipeshub_client.base_url}/api/v1/agents/{agent_key}",
                    headers=pipeshub_client.auth_headers,
                    timeout=pipeshub_client.timeout_seconds,
                )
            except Exception:
                pass

    def _upload_url(self, agent_key: str) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            "/conversations/attachments/upload"
        )

    def _delete_url(self, agent_key: str, record_id: str) -> str:
        return (
            f"{self.base_url}/api/v1/agents/{agent_key}"
            f"/conversations/attachments/{record_id}"
        )

    def test_upload_pdf_matches_openapi_spec_and_returns_attachment_ref(
        self,
        asana_pdf_blob: dict[str, Any],
        created_agent_key: str,
    ) -> None:
        multipart_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() != "content-type"
        }
        files = [
            (
                "files",
                (
                    asana_pdf_blob["originalname"],
                    io.BytesIO(asana_pdf_blob["buffer"]),
                    asana_pdf_blob["mimetype"],
                ),
            ),
        ]
        cleanup_record_ids: list[str] = []

        try:
            resp = requests.post(
                self._upload_url(created_agent_key),
                headers=multipart_headers,
                files=files,
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
            # Best-effort cleanup; these uploads create graph/file records.
            for record_id in cleanup_record_ids:
                try:
                    requests.delete(
                        self._delete_url(created_agent_key, record_id),
                        headers=self.headers,
                        timeout=self.timeout,
                    )
                except Exception:
                    pass

    def test_delete_uploaded_attachment_returns_204_with_empty_body(
        self,
        asana_pdf_blob: dict[str, Any],
        created_agent_key: str,
    ) -> None:
        multipart_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() != "content-type"
        }
        files = [
            (
                "files",
                (
                    asana_pdf_blob["originalname"],
                    io.BytesIO(asana_pdf_blob["buffer"]),
                    asana_pdf_blob["mimetype"],
                ),
            ),
        ]

        upload_resp = requests.post(
            self._upload_url(created_agent_key),
            headers=multipart_headers,
            files=files,
            timeout=self.timeout,
        )
        assert upload_resp.status_code == 200, f"{upload_resp.status_code}: {upload_resp.text}"

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

        delete_resp = requests.delete(
            self._delete_url(created_agent_key, record_id),
            headers=self.headers,
            timeout=self.timeout,
        )
        assert delete_resp.status_code == 204, f"{delete_resp.status_code}: {delete_resp.text}"
        assert delete_resp.text == ""
