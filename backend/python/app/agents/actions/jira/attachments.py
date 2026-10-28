"""Attach PipesHub records to Jira issues — shared by the Jira Cloud and Data Center toolsets.

`JiraAttachmentUploader` posts each file to ``/rest/api/{3|2}/issue/{key}/attachments``.
`JiraIssueAttachments` wires it into the create / update / comment tools and renders
files into descriptions and comments:

- Cloud bodies are ADF. Jira Cloud has no supported way to show an API-uploaded
  image inline (the ADF media node needs a Media Services id the REST API never
  returns — JRACLOUD-96384), so files become links to the attachment.
- Data Center bodies are wiki markup, where ``!name|thumbnail!`` shows an attached
  image and ``[^name]`` links any attached file.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal

import httpx

from app.agents.actions.util.attachment_delivery import (
    EMPTY_COMMENT_ERROR,
    DeliveryReport,
    PreparedAttachments,
    comment_would_be_empty,
    deliver_attachments,
    issue_result_key,
    prepare_attachments,
    replace_placeholders,
    send_upload,
    split_on_placeholders,
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
    from app.sources.external.jira.jira import JiraDataSource

logger = logging.getLogger(__name__)

__all__ = ["JiraAttachmentUploader", "JiraIssueAttachments"]

Flavor = Literal["cloud", "data_center"]

# Characters that end a wiki-markup image or attachment reference early.
_WIKI_UNSAFE = re.compile(r"[|!\[\]]")

# Over OAuth, Jira Cloud answers with URLs on the API gateway, which a browser can't open.
_OAUTH_GATEWAY = re.compile(r"^https://api\.atlassian\.com/ex/jira/[^/]+")

_UPLOAD_ERRORS = {
    HttpStatusCode.UNAUTHORIZED.value: "Jira did not accept the saved sign-in. Reconnect Jira, then try again.",
    HttpStatusCode.FORBIDDEN.value: (
        "Jira refused the upload: this account can't add attachments to the issue "
        "(Create Attachments permission or issue security)."
    ),
    HttpStatusCode.NOT_FOUND.value: "Jira couldn't find the issue, or this account can't see it.",
    413: "The file is larger than this Jira site allows.",
}


def _jira_error_text(response: HTTPResponse) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    messages = list(body.get("errorMessages") or [])
    messages += [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    return "; ".join(str(m) for m in messages)


class JiraAttachmentUploader:
    """Upload `ResolvedRecordContent` objects as attachments of one issue.

    `target.destination_id` is the issue key or id. One request per file, so each
    file gets its own outcome and one rejected file doesn't sink the rest.
    """

    max_total_bytes: int = DEFAULT_MAX_STAGE_BYTES

    def __init__(self, datasource: JiraDataSource, *, api_version: Literal["2", "3"] = "3") -> None:
        self._ds = datasource
        self._api_version = api_version
        self.max_single_bytes: int = DEFAULT_MAX_STAGE_BYTES
        self._preflight_done = False
        self._preflight_error: str | None = None

    async def preflight(self) -> str | None:
        """Stop early when the site has attachments off; adopt its per-file limit."""
        if self._preflight_done:
            return self._preflight_error
        self._preflight_done = True
        try:
            response = await self._ds.get_attachment_settings(api_version=self._api_version)
        except Exception as exc:
            # Unknown settings must not block: the upload itself reports a real refusal.
            logger.warning("Jira attachment settings unavailable: %s", type(exc).__name__)
            return None
        if response.status != HttpStatusCode.SUCCESS.value:
            return None
        try:
            settings = response.json()
        except Exception:
            return None
        if not isinstance(settings, dict):
            return None
        if settings.get("enabled") is False:
            self._preflight_error = "Attachments are turned off on this Jira site."
            return self._preflight_error
        limit = settings.get("uploadLimit")
        if isinstance(limit, int) and limit > 0:
            self.max_single_bytes = min(self.max_single_bytes, limit)
        return None

    def upload_filename(self, filename: str) -> str:
        if self._api_version == "2":
            return _WIKI_UNSAFE.sub("_", filename)
        return filename

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        return [await self._upload_one(target.destination_id, a) for a in attachments]

    async def _upload_one(self, issue_key: str, attachment: ResolvedRecordContent) -> AttachmentUploadResult:
        filename = self.upload_filename(attachment.filename)
        failed = AttachmentUploadResult(
            record_id=attachment.record_id,
            filename=attachment.filename,
            success=False,
            mime_type=attachment.mime_type,
        )
        try:
            response = await send_upload(lambda: self._ds.upload_issue_attachment(
                issue_key,
                filename,
                attachment.content,
                attachment.mime_type or "application/octet-stream",
                api_version=self._api_version,
            ))
        except httpx.TimeoutException:
            failed.error = "Jira did not answer in time; check the issue before attaching the file again."
            return failed
        except Exception as exc:
            logger.warning("Jira attachment upload failed for %s: %s", attachment.record_id, type(exc).__name__)
            failed.error = f"Upload failed: {type(exc).__name__}"
            return failed

        if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value):
            try:
                created = response.json()
                item = created[0] if isinstance(created, list) and created else created
            except Exception:
                item = {}
            item = item if isinstance(item, dict) else {}
            stored = item.get("filename") or filename
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=True,
                platform_id=str(item["id"]) if item.get("id") is not None else None,
                platform_url=item.get("content"),
                stored_filename=stored if stored != attachment.filename else None,
                mime_type=attachment.mime_type,
            )

        detail = _jira_error_text(response)
        message = _UPLOAD_ERRORS.get(response.status) or f"Jira returned HTTP {response.status}"
        failed.error = f"{message} {detail}".strip() if detail else message
        return failed


# ---------------------------------------------------------------------------
# Body rendering
# ---------------------------------------------------------------------------


def _adf_file_node(result: AttachmentUploadResult) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": result.display_name}
    if result.platform_url:
        node["marks"] = [{"type": "link", "attrs": {"href": result.platform_url}}]
    return node


def _adf_attachments_paragraph(results: Sequence[AttachmentUploadResult]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "Attachments: "}]
    for i, result in enumerate(results):
        if i:
            content.append({"type": "text", "text": ", "})
        content.append(_adf_file_node(result))
    return {"type": "paragraph", "content": content}


def adf_with_attachment_links(text: str | None, report: DeliveryReport) -> dict[str, Any]:
    """One paragraph of `text` with each placeholder turned into a link, then a list of the rest."""
    keyed = report.by_key()
    placed: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for literal, key in split_on_placeholders(text or ""):
        if literal:
            nodes.append({"type": "text", "text": literal})
        result = keyed.get(key) if key else None
        if result is not None and result.record_id not in placed:
            placed.add(result.record_id)
            nodes.append(_adf_file_node(result))

    content: list[dict[str, Any]] = []
    if nodes:
        content.append({"type": "paragraph", "content": nodes})
    rest = [r for r in report.uploaded if r.record_id not in placed]
    if rest:
        content.append(_adf_attachments_paragraph(rest))
    return {"type": "doc", "version": 1, "content": content}


def adf_append_attachment_links(doc: object, report: DeliveryReport) -> dict[str, Any]:
    """Existing ADF description with an attachments paragraph added at the end."""
    if isinstance(doc, dict) and doc.get("type") == "doc":
        updated = copy.deepcopy(doc)
        updated.setdefault("content", []).append(_adf_attachments_paragraph(report.uploaded))
        return updated
    return {"type": "doc", "version": 1, "content": [_adf_attachments_paragraph(report.uploaded)]}


def wiki_reference(result: AttachmentUploadResult) -> str:
    name = result.display_name
    return f"!{name}|thumbnail!" if result.is_image else f"[^{name}]"


def wiki_with_attachments(text: str | None, report: DeliveryReport) -> str:
    return replace_placeholders(text or "", report, wiki_reference, separator="\n")


# ---------------------------------------------------------------------------
# Tool integration
# ---------------------------------------------------------------------------


class JiraIssueAttachments:
    """Attachment steps for a Jira toolset; `flavor` picks the API version and body format."""

    def __init__(
        self,
        datasource: JiraDataSource,
        state: ChatState | None,
        *,
        flavor: Flavor,
        site_url: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        self._ds = datasource
        self._state = state
        self._flavor = flavor
        self._site_url_lookup = site_url
        self._target_app = "jira" if flavor == "cloud" else "jira_data_center"
        self.uploader = JiraAttachmentUploader(datasource, api_version="3" if flavor == "cloud" else "2")

    async def create_with_files(
        self,
        refs: list[str],
        attachment_display: str | None,
        description: str | None,
        create: Callable[[str | None], Awaitable[tuple[bool, str]]],
    ) -> tuple[bool, str]:
        """Resolve files, run `create(description)`, then attach to the new issue."""
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        ok, result = await create(self.without_placeholders(description))
        issue_key = issue_result_key(result) if ok else None
        if not issue_key:
            return ok, result
        report = await self.deliver(issue_key, prepared)
        warning = None
        if wants_inline(attachment_display):
            warning = await self.place_in_description(issue_key, report, description or "")
        return True, with_attachment_outcome(result, report, warning)

    async def update_with_files(
        self,
        issue_key: str,
        refs: list[str],
        attachment_display: str | None,
        description: str | None,
        update: Callable[[str | None], Awaitable[tuple[bool, str]]] | None,
    ) -> tuple[bool, str]:
        """Resolve files, run `update(description)` when there are field changes, then attach.

        With no field changes (`update` is None) only the files are attached; the call fails
        when none of them could be.
        """
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        if update is not None:
            ok, result = await update(self.without_placeholders(description))
            if not ok:
                return ok, result
        else:
            result = json.dumps({"message": f"Files sent to {issue_key}", "data": {"key": issue_key}})
        report = await self.deliver(issue_key, prepared)
        warning = None
        if wants_inline(attachment_display):
            warning = await self.place_in_description(issue_key, report, description)
        return update is not None or bool(report.uploaded), with_attachment_outcome(result, report, warning)

    async def comment_with_files(
        self,
        issue_key: str,
        refs: list[str],
        attachment_display: str | None,
        comment: str | dict[str, Any],
        add_comment: Callable[[str | dict[str, Any]], Awaitable[tuple[bool, str]]],
    ) -> tuple[bool, str]:
        """Attach files to the issue (Jira has no comment attachments), then post the comment."""
        if not isinstance(comment, dict) and comment_would_be_empty(str(comment or ""), attachment_display):
            return False, EMPTY_COMMENT_ERROR
        prepared = await self.prepare(refs)
        if prepared.abort_reason:
            return False, prepared.abort_payload()
        report = await self.deliver(issue_key, prepared)
        if not isinstance(comment, str):
            body = comment
        elif wants_inline(attachment_display) and report.uploaded:
            body = self.body(comment, report)
        else:
            body = strip_placeholders(comment)
        ok, result = await add_comment(body)
        # Reported even when the comment fails: the files are already on the issue.
        return ok, with_attachment_outcome(result, report)

    async def prepare(self, refs: list[str] | None) -> PreparedAttachments:
        return await prepare_attachments(self._state, refs, self.uploader)

    async def deliver(self, issue_key: str, prepared: PreparedAttachments) -> DeliveryReport:
        report = await deliver_attachments(
            self._state,
            prepared,
            self.uploader,
            AttachmentTarget(destination_id=issue_key, display_name=issue_key),
            target_app=self._target_app,
        )
        await self._use_site_links(report)
        return report

    async def _use_site_links(self, report: DeliveryReport) -> None:
        gateway_links = [r for r in report.uploaded if r.platform_url and _OAUTH_GATEWAY.match(r.platform_url)]
        if not gateway_links or self._site_url_lookup is None:
            return
        try:
            site = await self._site_url_lookup()
        except Exception as exc:
            # The files are already on the issue; a failure here must not read as a failed upload.
            logger.warning("Jira site URL lookup failed: %s", type(exc).__name__)
            return
        if not site:
            return
        for result in gateway_links:
            result.platform_url = _OAUTH_GATEWAY.sub(lambda _: site.rstrip("/"), result.platform_url, count=1)

    @staticmethod
    def without_placeholders(text: str | None) -> str | None:
        return strip_placeholders(text) if text else text

    def body(self, text: str | None, report: DeliveryReport) -> dict[str, Any] | str:
        """Description or comment body with the uploaded files placed in it."""
        if self._flavor == "cloud":
            return adf_with_attachment_links(text, report)
        return wiki_with_attachments(text, report)

    async def place_in_description(self, issue_key: str, report: DeliveryReport, text: str | None) -> str | None:
        """Put the uploaded files into the issue description; returns a warning on failure.

        `text` is the description the tool just wrote (placeholders resolved against it).
        With no `text`, the current description is kept and the files are appended to it.
        """
        if not report.uploaded:
            return None
        try:
            if text is not None:
                description: dict[str, Any] | str = self.body(text, report)
            else:
                description = await self._appended_description(issue_key, report)
            if self._flavor == "cloud":
                response = await self._ds.edit_issue(issueIdOrKey=issue_key, fields={"description": description})
            else:
                response = await self._ds.edit_issue_v2(issueIdOrKey=issue_key, fields={"description": description})
        except Exception as exc:
            logger.warning("Placing attachments in %s description failed: %s", issue_key, type(exc).__name__)
            return "Files were attached, but could not be added to the description."
        if response.status not in (HttpStatusCode.SUCCESS.value, HttpStatusCode.NO_CONTENT.value):
            return (
                "Files were attached, but could not be added to the description "
                f"(Jira returned HTTP {response.status})."
            )
        return None

    async def _appended_description(self, issue_key: str, report: DeliveryReport) -> dict[str, Any] | str:
        current = await self._current_description(issue_key)
        if self._flavor == "cloud":
            return adf_append_attachment_links(current, report)
        references = "\n".join(wiki_reference(r) for r in report.uploaded)
        return f"{current}\n\n{references}" if current else references

    async def _current_description(self, issue_key: str) -> object:
        """The stored description (None when the issue has none).

        Raises when it can't be read: treating a failed read as an empty description
        would replace the real one with only the file links.
        """
        get_issue = self._ds.get_issue if self._flavor == "cloud" else self._ds.get_issue_v2
        response = await get_issue(issueIdOrKey=issue_key, fields=["description"])
        if response.status != HttpStatusCode.SUCCESS.value:
            raise RuntimeError(f"Jira returned HTTP {response.status} reading {issue_key}")
        body = response.json()
        fields = body.get("fields") if isinstance(body, dict) else None
        if not isinstance(fields, dict):
            raise RuntimeError(f"Jira returned no fields for {issue_key}")
        return fields.get("description")
