"""`SalesforceAttachmentUploader` — Salesforce Files upload adapter.

Salesforce Files (ContentVersion) accept files via a JSON POST with
base64-encoded `VersionData`. The REST API caps JSON bodies at 50 MB,
which limits raw file content to ~37.5 MB after base64 expansion.
We enforce a 25 MiB raw cap (from the existing blob-staging constant) to
stay well within that limit.

Optionally, when `link_to_salesforce_id` is set on the target, the uploader
creates a `ContentDocumentLink` in the same call, folding the separate
`attach_file_to_record` step into one.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Sequence

from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES
from app.services.record_content.models import ResolvedRecordContent

logger = logging.getLogger(__name__)

__all__ = ["SalesforceAttachmentUploader"]

_SF_CONTENT_VERSION = "ContentVersion"
_SF_CONTENT_DOCUMENT_LINK = "ContentDocumentLink"


class SalesforceAttachmentUploader:
    """Upload `ResolvedRecordContent` objects as Salesforce Files.

    `target.destination_id` is the Salesforce record ID to link the file to
    (optional — may be empty to upload without linking).
    `target.extra["share_type"]` and `target.extra["visibility"]` control
    `ContentDocumentLink` fields.
    """

    max_single_bytes: int = DEFAULT_MAX_STAGE_BYTES
    max_total_bytes: int = DEFAULT_MAX_STAGE_BYTES

    def __init__(self, client: Any, api_version: str = "59.0") -> None:
        self._client = client
        self._api_version = api_version

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        """Upload each attachment as a ContentVersion and optionally link it."""
        results: list[AttachmentUploadResult] = []
        link_to_id = target.destination_id or None
        share_type = target.extra.get("share_type", "V")
        visibility = target.extra.get("visibility") or None

        for attachment in attachments:
            result = await self._upload_one(
                attachment,
                link_to_id=link_to_id,
                share_type=share_type,
                visibility=visibility,
            )
            results.append(result)

        return results

    async def _upload_one(
        self,
        attachment: ResolvedRecordContent,
        *,
        link_to_id: str | None,
        share_type: str,
        visibility: str | None,
    ) -> AttachmentUploadResult:
        content_b64 = base64.b64encode(attachment.content).decode("ascii")
        payload = {
            "Title": attachment.filename,
            "PathOnClient": attachment.filename,
            "VersionData": content_b64,
        }

        cv_response = await self._client.sobject_create(
            api_version=self._api_version,
            sobject=_SF_CONTENT_VERSION,
            data=payload,
        )
        if not cv_response.success:
            error_detail = getattr(cv_response, "message", None) or cv_response.error or "ContentVersion creation failed"
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error=error_detail,
            )

        cv_data = cv_response.data or {}
        content_version_id = cv_data.get("id") or cv_data.get("Id")
        if not content_version_id:
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error="ContentVersion created but no Id returned",
            )

        # Resolve ContentDocumentId from the ContentVersion.
        content_document_id: str | None = None
        try:
            query = f"SELECT ContentDocumentId FROM ContentVersion WHERE Id = '{content_version_id}'"
            query_resp = await self._client.soql_query(
                api_version=self._api_version, q=query
            )
            if query_resp.success and query_resp.data:
                records = (query_resp.data or {}).get("records", [])
                if records:
                    content_document_id = records[0].get("ContentDocumentId")
        except Exception as exc:
            logger.warning("SalesforceAttachmentUploader: failed to resolve ContentDocumentId: %s", exc)

        # Link to Salesforce record if requested.
        if link_to_id and content_document_id:
            link_payload: dict[str, str] = {
                "ContentDocumentId": content_document_id,
                "LinkedEntityId": link_to_id,
                "ShareType": share_type,
            }
            if visibility is not None:
                link_payload["Visibility"] = visibility
            link_response = await self._client.sobject_create(
                api_version=self._api_version,
                sobject=_SF_CONTENT_DOCUMENT_LINK,
                data=link_payload,
            )
            if not link_response.success:
                error_detail = getattr(link_response, "message", None) or link_response.error
                return AttachmentUploadResult(
                    record_id=attachment.record_id,
                    filename=attachment.filename,
                    success=False,
                    platform_id=content_document_id,
                    error=(
                        f"ContentVersion created (ContentDocumentId={content_document_id}) "
                        f"but ContentDocumentLink failed: {error_detail}"
                    ),
                )

        return AttachmentUploadResult(
            record_id=attachment.record_id,
            filename=attachment.filename,
            success=True,
            platform_id=content_document_id,
        )
