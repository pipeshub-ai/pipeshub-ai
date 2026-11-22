"""Attach PipesHub records to something a tool creates or updates.

Trackers and wikis (Jira, Confluence, ...) cannot take files in the call that
creates an issue or page, so a tool that accepts `attachment_record_ids` runs:

1. `prepare_attachments` — check the destination and resolve every file
   *before* writing anything, so a request whose files can't be sent never
   creates a ticket.
2. the tool's own create / update, unchanged.
3. `deliver_attachments` — upload each file and audit it.
4. `DeliveryReport.merge_into` — add per-file outcomes to the tool result.

A record that was created stays a success even when some uploads fail: the
result carries `partial: true` and the failures, so the model reports them
instead of retrying the create and making a duplicate.

Tools that place files in a body use `{{attachment:<record id or file name>}}`
placeholders; `replace_placeholders` swaps them for platform markup.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
    IAttachmentUploader,
    SupportsPreflight,
)
from app.agents.actions.util.attachments import (
    AttachmentBundle,
    emit_attachment_audit,
    resolve_attachments,
)
from app.core import org_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.modules.agents.qna.chat_state import ChatState
    from app.sources.client.http.http_response import HTTPResponse

__all__ = [
    "ATTACHMENT_DISPLAY_INLINE",
    "EMPTY_COMMENT_ERROR",
    "DeliveryReport",
    "PreparedAttachments",
    "attachment_display_parameter",
    "comment_would_be_empty",
    "deliver_attachments",
    "has_text",
    "issue_result_key",
    "prepare_attachments",
    "replace_placeholders",
    "send_upload",
    "split_on_placeholders",
    "strip_placeholders",
    "wants_inline",
    "with_attachment_outcome",
]

# Uploads are not idempotent: only a 429 (refused, nothing stored) is safe to resend.
_MAX_RATE_LIMIT_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 10.0
_TOO_MANY_REQUESTS = 429

ATTACHMENT_DISPLAY_INLINE = "inline"
ATTACHMENT_DISPLAY_ATTACHMENT = "attachment"

_PLACEHOLDER = re.compile(r"\{\{\s*attachment\s*:\s*([^{}]+?)\s*\}\}")

EMPTY_COMMENT_ERROR = json.dumps({
    "error": "Comment text is required and cannot be empty",
    "guidance": "To attach files without a comment, use add_attachments.",
})


def attachment_display_parameter(*, inline_note: str) -> ToolParameter:
    """The `attachment_display` parameter, shared by every tool that can place files in a body."""
    return ToolParameter(
        name="attachment_display",
        type=ParameterType.STRING,
        required=False,
        enum=[ATTACHMENT_DISPLAY_ATTACHMENT, ATTACHMENT_DISPLAY_INLINE],
        description=(
            "How files from attachment_record_ids appear. 'attachment' (default): attached only. "
            "'inline': also shown in the body — put {{attachment:<record id or file name>}} where a "
            f"file should appear; files without a placeholder go at the end. {inline_note}"
        ),
    )


def wants_inline(attachment_display: str | None) -> bool:
    return (attachment_display or "").strip().lower() == ATTACHMENT_DISPLAY_INLINE


def strip_placeholders(text: str) -> str:
    return _PLACEHOLDER.sub("", text)


def has_text(text: str | None) -> bool:
    """True when `text` still says something once placeholders are removed.

    A description or page body that is blank, or only placeholders, is "not given": the tool
    keeps the current one and appends the files, rather than replacing it with nothing.
    """
    return bool(text and strip_placeholders(text).strip())


def comment_would_be_empty(comment: str, attachment_display: str | None) -> bool:
    """True when a comment has no text of its own and 'inline' won't add the files to it.

    Comment tools upload before they post, so they check this first: a comment that failed
    after the upload would leave the files on the issue or page, and a retry would add them twice.
    """
    return not wants_inline(attachment_display) and not strip_placeholders(comment).strip()


def split_on_placeholders(text: str) -> list[tuple[str, str | None]]:
    """Split text into (literal, placeholder key or None) pieces, in order."""
    pieces: list[tuple[str, str | None]] = []
    last = 0
    for match in _PLACEHOLDER.finditer(text):
        pieces.append((text[last:match.start()], match.group(1)))
        last = match.end()
    pieces.append((text[last:], None))
    return pieces


@dataclass
class PreparedAttachments:
    """Files resolved before the write, or the reason the write must not happen."""

    requested: int = 0
    bundle: AttachmentBundle = field(default_factory=AttachmentBundle)
    abort_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.requested > 0

    def abort_payload(self) -> str:
        payload: dict[str, Any] = {
            "error": self.abort_reason or "None of the files could be attached.",
            "guidance": (
                "Nothing was created or changed. Tell the user which files could not be "
                "attached, or retry without attachment_record_ids if they agree."
            ),
        }
        if self.bundle.failures:
            payload["attachment_failures"] = [f.to_dict() for f in self.bundle.failures]
        return json.dumps(payload)


@dataclass
class DeliveryReport:
    uploaded: list[AttachmentUploadResult] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.failed)

    def by_key(self) -> dict[str, AttachmentUploadResult]:
        """Uploaded files keyed by record id and by original file name, for placeholders."""
        keyed: dict[str, AttachmentUploadResult] = {}
        for result in self.uploaded:
            keyed.setdefault(result.filename, result)
            keyed[result.record_id] = result
        return keyed

    def to_dict(self) -> dict[str, Any]:
        return {
            "uploaded": [r.to_dict() for r in self.uploaded],
            "failed": list(self.failed),
        }

    def summary(self) -> str:
        total = len(self.uploaded) + len(self.failed)
        text = f"{len(self.uploaded)} of {total} file(s) attached"
        if self.failed:
            reasons = "; ".join(
                f"{f.get('filename') or f.get('ref')}: {f.get('error')}" for f in self.failed
            )
            text += f". Not attached: {reasons}"
        return text

    def merge_into(self, result_json: str) -> str:
        """Add the per-file outcome to a tool's JSON result, keeping its existing keys."""
        try:
            result = json.loads(result_json)
        except (TypeError, ValueError):
            result = {"result": result_json}
        if not isinstance(result, dict):
            result = {"result": result}
        result["attachments"] = self.to_dict()
        if self.partial:
            result["partial"] = True
        message = result.get("message")
        result["message"] = f"{message}. {self.summary()}" if message else self.summary()
        return json.dumps(result)


async def prepare_attachments(
    state: ChatState | None,
    refs: list[str] | None,
    uploader: IAttachmentUploader,
) -> PreparedAttachments:
    """Check the destination and resolve `refs` within the uploader's limits.

    Inactive (nothing to do) when `refs` is empty. Sets `abort_reason` when files
    were asked for but none can be sent.
    """
    if not refs:
        return PreparedAttachments()

    prepared = PreparedAttachments(requested=len(refs))

    if isinstance(uploader, SupportsPreflight):
        reason = await uploader.preflight()
        if reason:
            prepared.abort_reason = reason
            return prepared

    prepared.bundle = await resolve_attachments(
        state,
        refs,
        max_total_bytes=uploader.max_total_bytes,
        max_single_bytes=uploader.max_single_bytes,
    )
    if not prepared.bundle.resolved:
        prepared.abort_reason = "None of the requested files could be attached."
    return prepared


def _audit_org_id() -> str:
    """The org the request is bound to. Without one the audit line names no org; the upload goes ahead."""
    try:
        return org_context.current_org_id()
    except (org_context.MissingOrgContext, org_context.SystemContextNotAllowed):
        return ""


async def deliver_attachments(
    state: ChatState | None,
    prepared: PreparedAttachments,
    uploader: IAttachmentUploader,
    target: AttachmentTarget,
    *,
    target_app: str,
) -> DeliveryReport:
    """Upload the resolved files to `target`, auditing each one."""
    report = DeliveryReport(
        failed=[
            {"ref": f.ref, "error": f.error, "error_type": f.error_type}
            for f in prepared.bundle.failures
        ]
    )
    if not prepared.bundle.resolved:
        return report

    results = await uploader.upload(target, prepared.bundle.resolved)

    org_id = _audit_org_id()
    user_id = (state.get("user_id") if state else None) or ""
    sizes = {r.record_id: r.size_bytes for r in prepared.bundle.resolved}

    for result in results:
        emit_attachment_audit(
            org_id=org_id,
            user_id=user_id,
            record_id=result.record_id,
            filename=result.filename,
            target_app=target_app,
            destination=target.destination_id,
            success=result.success,
            error=result.error,
            size_bytes=sizes.get(result.record_id),
        )
        if result.success:
            report.uploaded.append(result)
        else:
            report.failed.append(
                {"ref": result.record_id, "filename": result.filename, "error": result.error}
            )
    return report


def _retry_after_seconds(response: HTTPResponse, attempt: int) -> float:
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    try:
        delay = float(raw) if raw is not None else 2.0 ** attempt
    except ValueError:
        delay = 2.0 ** attempt
    return min(max(delay, 0.0), _MAX_RETRY_AFTER_SECONDS)


async def send_upload(send: Callable[[], Awaitable[HTTPResponse]]) -> HTTPResponse:
    """Send one upload request, resending only after a 429 (honouring Retry-After)."""
    response = await send()
    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        if response.status != _TOO_MANY_REQUESTS:
            break
        await asyncio.sleep(_retry_after_seconds(response, attempt))
        response = await send()
    return response


def issue_result_key(result_json: str, key: str = "key") -> str | None:
    """`data.<key>` of a tool's JSON result — the id of what it just created."""
    try:
        data = json.loads(result_json).get("data") or {}
    except (TypeError, ValueError, AttributeError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return str(value) if value is not None else None


def with_attachment_outcome(result_json: str, report: DeliveryReport, warning: str | None = None) -> str:
    """`report.merge_into` plus a warning, kept alongside any warning the tool already set."""
    merged = json.loads(report.merge_into(result_json))
    if warning:
        merged["warning"] = f"{merged['warning']}; {warning}" if merged.get("warning") else warning
    return json.dumps(merged)


def replace_placeholders(
    text: str,
    report: DeliveryReport,
    render: Callable[[AttachmentUploadResult], str],
    *,
    separator: str = "",
    wrap_appended: Callable[[str], str] | None = None,
) -> str:
    """Swap placeholders for `render(file)`; append files no placeholder named.

    A placeholder naming a file that wasn't uploaded is removed, so no literal
    `{{attachment:...}}` ever reaches the destination. `wrap_appended` wraps each
    appended file (e.g. in its own paragraph).
    """
    keyed = report.by_key()
    placed: set[str] = set()

    def _swap(match: re.Match[str]) -> str:
        result = keyed.get(match.group(1))
        if result is None or result.record_id in placed:
            return ""
        placed.add(result.record_id)
        return render(result)

    body = _PLACEHOLDER.sub(_swap, text)
    rest = [render(r) for r in report.uploaded if r.record_id not in placed]
    if wrap_appended is not None:
        rest = [wrap_appended(item) for item in rest]
    if rest:
        body = separator.join([body, *rest]) if body else separator.join(rest)
    return body
