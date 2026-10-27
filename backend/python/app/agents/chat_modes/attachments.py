"""This turn's attachments for `/chat/stream`: which records the run may search.

The attachments' content is not rendered here. `PipesHubAgentFactory.create`
puts it on the current request: documents within the attachments' share of
the model's budget, images through the request's image admission, or a
"[Image attached by user: …]" line for a text-only model
(`hooks/attachment_resolver.resolve_attachments_for_goal`). A second copy used
to go into the goal's constraints, that is the system prompt: every document
was sent twice, unbounded, and one large enough left the messages no budget at
all; images were described as "not visible to you" while they were attached.

Both kinds' `virtualRecordId`s are merged into the retrieval filter scope
(`connector_ids` passed to `search_internal_knowledge` / the `filters`
prefetch uses) so a retrieval call -- prefetch or a follow-up tool call --
can surface the attachment's indexed content alongside the rest of the
knowledge base. A client-supplied id is included only after
`check_record_access_with_details` grants this user the record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.utils.attachment_mime_types import DOC_ATTACHMENT_MIME_TYPES
from app.utils.record_access import caller_can_read_virtual_record

if TYPE_CHECKING:
    import logging

__all__ = ["ResolvedAttachments", "resolve_attachments"]


@dataclass
class ResolvedAttachments:
    """Everything the bridge needs from this turn's attachments."""

    virtual_record_ids: list[str] = field(default_factory=list)
    has_image_attachments: bool = False


def _is_image(mime_type: str) -> bool:
    return mime_type.lower().startswith("image/")


def _is_doc(mime_type: str) -> bool:
    return mime_type.lower() in DOC_ATTACHMENT_MIME_TYPES


async def resolve_attachments(
    attachments: list[dict[str, Any]] | None,
    *,
    org_id: str,
    logger: "logging.Logger",
    user_id: str | None = None,
    graph_provider: Any = None,
    is_service_account: bool = False,
) -> ResolvedAttachments:
    """The current turn's `query_info["attachments"]` the user may read, as
    retrieval-scope record IDs."""
    if not attachments:
        return ResolvedAttachments()

    virtual_record_ids: list[str] = []
    has_images = False
    for att in attachments:
        if not isinstance(att, dict):
            continue
        mime = att.get("mimeType") or ""
        if not _is_doc(mime) and not _is_image(mime):
            continue
        vrid = att.get("virtualRecordId") or ""
        if vrid and not await caller_can_read_virtual_record(
            graph_provider,
            user_id=user_id,
            org_id=org_id,
            virtual_record_id=vrid,
            logger=logger,
            is_service_account=is_service_account,
        ):
            continue
        if vrid:
            virtual_record_ids.append(vrid)
        has_images = has_images or _is_image(mime)

    return ResolvedAttachments(virtual_record_ids=virtual_record_ids, has_image_attachments=has_images)
