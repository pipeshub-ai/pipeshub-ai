"""Attach PipesHub records to Confluence pages — shared by the Confluence Cloud and Data Center toolsets.

`ConfluenceAttachmentUploader` adds each file with the v1 upload endpoint (v2 has none).
It never versions an existing attachment: when the page already has a file of that name,
the new one is stored as ``name (2).ext``.

`ConfluencePageAttachments` wires it into the page and comment tools. Inline files are
placed with storage-format markup — ``<ac:image>`` for images, ``<ac:link>`` for other
files — which resolves by file name against the page's own attachments.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

import httpx

from app.agents.actions.util.attachment_delivery import (
    EMPTY_COMMENT_ERROR,
    DeliveryReport,
    PreparedAttachments,
    comment_would_be_empty,
    deliver_attachments,
    has_text,
    issue_result_key,
    prepare_attachments,
    replace_placeholders,
    send_upload,
    strip_placeholders,
    wants_inline,
    with_attachment_outcome,
)
from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES
from app.sources.client.http.exception.exception import HttpStatusCode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.modules.agents.qna.chat_state import ChatState
    from app.services.record_content.models import ResolvedRecordContent
    from app.sources.client.http.http_response import HTTPResponse
    from app.sources.external.confluence.confluence import ConfluenceDataSource

logger = logging.getLogger(__name__)

__all__ = [
    "CONFLUENCE_INLINE_NOTE",
    "ConfluenceAttachmentUploader",
    "ConfluencePageAttachments",
    "storage_reference",
]

Flavor = Literal["cloud", "data_center"]

CONFLUENCE_INLINE_NOTE = "Images are shown in the page; other files are linked."

# Alternative names tried when the page already has a file of the same name.
_MAX_NAME_ATTEMPTS = 3

_UPLOAD_ERRORS = {
    HttpStatusCode.UNAUTHORIZED.value: (
        "Confluence did not accept the saved sign-in for uploads. With OAuth, an admin must add the "
        "write:attachment:confluence scope to the Atlassian app, set 'Allow file uploads' to Yes on the "
        "Confluence OAuth app in PipesHub, and the user must reconnect Confluence."
    ),
    HttpStatusCode.FORBIDDEN.value: (
        "Confluence refused the upload: attachments are turned off, or this account can't add "
        "attachments to the page."
    ),
    HttpStatusCode.NOT_FOUND.value: (
        "Confluence couldn't find the page, this account can't see it, or the file is larger than "
        "the site allows."
    ),
    413: "The file is larger than this Confluence site allows.",
}


def _numbered(filename: str, n: int) -> str:
    path = PurePosixPath(filename)
    return f"{path.stem} ({n}){path.suffix}" if path.suffix else f"{filename} ({n})"


def _is_duplicate_name(response: HTTPResponse) -> bool:
    if response.status != HttpStatusCode.BAD_REQUEST.value:
        return False
    try:
        text = response.text().lower()
    except Exception:
        return False
    return "same file name" in text or "already exists" in text


def _error_text(response: HTTPResponse) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    return str(body.get("message") or "") if isinstance(body, dict) else ""


class ConfluenceAttachmentUploader:
    """Upload `ResolvedRecordContent` objects as attachments of one page (`target.destination_id`)."""

    max_single_bytes: int = DEFAULT_MAX_STAGE_BYTES
    max_total_bytes: int = DEFAULT_MAX_STAGE_BYTES

    def __init__(self, datasource: ConfluenceDataSource) -> None:
        self._ds = datasource

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        return [await self._upload_one(target.destination_id, a) for a in attachments]

    async def _upload_one(self, page_id: str, attachment: ResolvedRecordContent) -> AttachmentUploadResult:
        failed = AttachmentUploadResult(
            record_id=attachment.record_id,
            filename=attachment.filename,
            success=False,
            mime_type=attachment.mime_type,
        )
        name = attachment.filename
        try:
            for attempt in range(1, _MAX_NAME_ATTEMPTS + 1):
                response = await send_upload(lambda n=name: self._ds.upload_content_attachment(
                    page_id, n, attachment.content, attachment.mime_type or "application/octet-stream",
                ))
                if not _is_duplicate_name(response) or attempt == _MAX_NAME_ATTEMPTS:
                    break
                name = _numbered(attachment.filename, attempt + 1)
        except httpx.TimeoutException:
            failed.error = "Confluence did not answer in time; check the page before attaching the file again."
            return failed
        except Exception as exc:
            logger.warning("Confluence attachment upload failed for %s: %s", attachment.record_id, type(exc).__name__)
            failed.error = f"Upload failed: {type(exc).__name__}"
            return failed

        if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
            try:
                data = response.json()
            except Exception:
                data = {}
            data = data if isinstance(data, dict) else {}
            results = data.get("results")
            item = results[0] if isinstance(results, list) and results else data
            links = item.get("_links") or {}
            base = (data.get("_links") or {}).get("base") or links.get("base")
            download = links.get("download")
            stored = item.get("title") or name
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=True,
                platform_id=str(item["id"]) if item.get("id") is not None else None,
                platform_url=f"{base}{download}" if base and download else None,
                stored_filename=stored if stored != attachment.filename else None,
                mime_type=attachment.mime_type,
            )

        if _is_duplicate_name(response):
            failed.error = "The page already has files with this name; rename the file and try again."
            return failed
        detail = _error_text(response)
        message = _UPLOAD_ERRORS.get(response.status) or f"Confluence returned HTTP {response.status}"
        failed.error = f"{message} {detail}".strip() if detail else message
        return failed


def storage_reference(result: AttachmentUploadResult) -> str:
    """Storage-format markup that shows (image) or links (other file) a page attachment."""
    name = html.escape(result.display_name, quote=True)
    tag = "ac:image" if result.is_image else "ac:link"
    return f'<{tag}><ri:attachment ri:filename="{name}" /></{tag}>'


def _paragraph(markup: str) -> str:
    return f"<p>{markup}</p>"


class ConfluencePageAttachments:
    """Attachment steps for a Confluence toolset.

    `to_storage` is the toolset's own conversion of the content it sends, so files are
    placed into exactly the body the page received.
    """

    def __init__(
        self,
        datasource: ConfluenceDataSource,
        state: ChatState | None,
        *,
        flavor: Flavor,
        to_storage: Callable[[str], str] = lambda text: text,
    ) -> None:
        self._ds = datasource
        self._state = state
        self._flavor = flavor
        self._to_storage = to_storage
        self._target_app = "confluence" if flavor == "cloud" else "confluence_data_center"
        self.uploader = ConfluenceAttachmentUploader(datasource)

    async def prepare(self, refs: list[str] | None) -> PreparedAttachments:
        return await prepare_attachments(self._state, refs, self.uploader)

    async def deliver(self, page_id: str, prepared: PreparedAttachments) -> DeliveryReport:
        return await deliver_attachments(
            self._state,
            prepared,
            self.uploader,
            AttachmentTarget(destination_id=page_id, display_name=page_id),
            target_app=self._target_app,
        )

    @staticmethod
    def without_placeholders(text: str | None) -> str | None:
        return strip_placeholders(text) if text else text

    async def create_with_files(
        self,
        refs: list[str],
        attachment_display: str | None,
        content: str,
        create: Callable[[str], Awaitable[tuple[bool, str]]],
    ) -> tuple[bool, str]:
        """Resolve files, run `create(content)`, attach to the new page, then place them inline."""
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        ok, result = await create(strip_placeholders(content))
        page_id = issue_result_key(result, "id") if ok else None
        if not page_id:
            return ok, result
        report = await self.deliver(page_id, prepared)
        warning = None
        if wants_inline(attachment_display):
            warning = await self.place_in_page(page_id, report, content)
        return True, with_attachment_outcome(result, report, warning)

    async def update_with_files(
        self,
        page_id: str,
        refs: list[str],
        attachment_display: str | None,
        content: str | None,
        update: Callable[[str | None], Awaitable[tuple[bool, str]]] | None,
    ) -> tuple[bool, str]:
        """Resolve files, run `update(content)` when there are changes, attach, then place inline.

        With no changes (`update` is None) only the files are attached; the call fails when
        none of them could be.
        """
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        if update is not None:
            ok, result = await update(self.without_placeholders(content))
            if not ok:
                return ok, result
        else:
            result = json.dumps({"message": f"Files sent to page {page_id}", "data": {"id": page_id}})
        report = await self.deliver(page_id, prepared)
        warning = None
        if wants_inline(attachment_display):
            warning = await self.place_in_page(page_id, report, content)
        return update is not None or bool(report.uploaded), with_attachment_outcome(result, report, warning)

    async def comment_with_files(
        self,
        page_id: str,
        refs: list[str],
        attachment_display: str | None,
        comment: str,
        add_comment: Callable[[str], Awaitable[tuple[bool, str]]],
    ) -> tuple[bool, str]:
        """Attach files to the page, then post the comment (naming them when inline)."""
        if comment_would_be_empty(str(comment or ""), attachment_display):
            return False, EMPTY_COMMENT_ERROR
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        report = await self.deliver(page_id, prepared)
        text = strip_placeholders(str(comment or "")).rstrip()
        if wants_inline(attachment_display) and report.uploaded:
            text = self._with_file_names(text, report)
        ok, result = await add_comment(text)
        # Reported even when the comment fails: the files are already on the page.
        return ok, with_attachment_outcome(result, report)

    def _with_file_names(self, text: str, report: DeliveryReport) -> str:
        line = "Attached to this page: " + ", ".join(r.display_name for r in report.uploaded)
        # Data Center posts a comment that starts with a tag as storage XHTML as-is
        # (`_to_storage_body`), so a bare '&' or '<' in a file name would make it invalid.
        if self._flavor == "data_center" and text.strip().startswith("<"):
            return text + _paragraph(html.escape(line))
        return f"{text}\n\n{line}" if text else line

    async def place_in_page(self, page_id: str, report: DeliveryReport, content: str | None) -> str | None:
        """Write the uploaded files into the page body; returns a warning on failure.

        With `content` (what the tool just wrote, placeholders intact) the body is rebuilt from
        it. Without, or when it is blank once placeholders are removed, the files are appended
        to the current body.
        """
        if not report.uploaded:
            return None
        try:
            for _ in range(2):
                current = await self._current_page(page_id)
                if current is None:
                    return "Files were attached, but the page could not be read to place them."
                if content is not None and has_text(content):
                    body = replace_placeholders(
                        self._to_storage(content), report, storage_reference, wrap_appended=_paragraph,
                    )
                else:
                    body = current["body"] + "".join(_paragraph(storage_reference(r)) for r in report.uploaded)
                response = await self._write_page(page_id, current, body)
                if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value):
                    return None
                if response.status != HttpStatusCode.CONFLICT.value:
                    break
        except Exception as exc:
            logger.warning("Placing attachments in page %s failed: %s", page_id, type(exc).__name__)
            return "Files were attached, but could not be shown in the page."
        return (
            "Files were attached, but could not be shown in the page "
            f"(Confluence returned HTTP {response.status})."
        )

    async def _current_page(self, page_id: str) -> dict[str, Any] | None:
        if self._flavor == "cloud":
            response = await self._ds.get_page_by_id(id=int(page_id), body_format="storage")
        else:
            response = await self._ds.get_page_content_v1(page_id=page_id, expand="body.storage,version")
        if response.status != HttpStatusCode.SUCCESS.value:
            return None
        data = response.json()
        return {
            "raw": data,
            "title": data.get("title", ""),
            "version": int((data.get("version") or {}).get("number") or 1),
            "body": ((data.get("body") or {}).get("storage") or {}).get("value") or "",
        }

    async def _write_page(self, page_id: str, current: dict[str, Any], body: str) -> HTTPResponse:
        storage = {"storage": {"value": body, "representation": "storage"}}
        if self._flavor == "cloud":
            raw = current["raw"]
            return await self._ds.update_page(id=int(page_id), body={
                "id": raw.get("id") or str(page_id),
                "status": raw.get("status") or "current",
                "spaceId": raw.get("spaceId"),
                "title": current["title"],
                "version": {"number": current["version"] + 1},
                "body": storage,
            })
        return await self._ds.update_content_v1(content_id=page_id, body={
            "id": str(page_id),
            "type": "page",
            "title": current["title"],
            "version": {"number": current["version"] + 1},
            "body": storage,
        })
