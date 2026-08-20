"""
Attachment parsing and comment block building for the GitHub Teams connector.

Shared between the teams and personal connectors (issues.py / pull_requests.py
both delegate here). Responsibilities:
- Parse GitHub's ``user-attachments`` markdown/HTML syntax and extract attachments.
- Embed images inline as base64 for content blocks.
- Build ``FileRecord`` RecordUpdates from attachment lists.
- Build ``ChildRecord`` / ``CommentAttachment`` references for embedding in blocks.
- Build issue and PR comment ``BlockGroup`` lists (fixing the personal connector's
  review-comment threading: all review comments on the same file path are grouped
  into a single 2D comment thread, not one single-comment "thread" per comment).
"""

from __future__ import annotations

import base64
import datetime
import os
import re
import uuid
from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.models.blocks import (
    Block,
    BlockComment,
    BlockGroup,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import FileRecord, Record, RecordGroupType, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .constants import ATTACHMENT_MAX_SIZE_BYTES, PR_FILE_INLINE_CONTENT_MAX_BYTES
from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_GITHUB_ATTACHMENT_HOST = "github.com"
_GITHUB_ATTACHMENT_PATH_PREFIX = "/user-attachments/"
_GITHUB_ATTACHMENT_ASSET_PREFIX = "/user-attachments/assets/"

_EXTENSION_TO_MIME: dict[str, str] = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg",
    "gif": "gif", "webp": "webp", "bmp": "bmp", "svg": "svg+xml",
}

_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)


def _image_format_from_bytes(image_bytes: bytes) -> str | None:
    """MIME subtype sniffed from the image's magic bytes.

    GitHub ``user-attachments/assets`` URLs are extension-less, so a URL-based
    guess labels every image ``png`` regardless of the actual format — the
    bytes are the only reliable source.
    """
    for magic, fmt in _IMAGE_MAGIC:
        if image_bytes.startswith(magic):
            return fmt
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes.lstrip()[:5] in (b"<?xml", b"<svg ") or image_bytes.lstrip().startswith(b"<svg"):
        return "svg+xml"
    return None

# Unicode private-use sentinels: markdown authors cannot type them, and no
# regex in clean_github_content matches them, so a placeholder cannot be
# mangled by the link pass that runs after the image pass.
_IMG_TOKEN_OPEN = ""
_IMG_TOKEN_CLOSE = ""


def _image_placeholder(slot: int) -> str:
    """Marker standing in for an image while the body is being cleaned.

    Surrounded by blank lines because the data URI that replaces it must end up
    block-level: docling only lifts an image into an IMAGE block when it stands
    alone. Inline, the payload stays in the paragraph's TEXT block and the
    record dies on the validator's TEXT_DATA_CONTAINS_BASE64_IMAGE check.
    """
    return f"\n\n{_IMG_TOKEN_OPEN}{slot}{_IMG_TOKEN_CLOSE}\n\n"


def _sanitize_alt_text(alt: str | None) -> str:
    """Alt text safe inside ``![...]``.

    A bracket or newline would close the image syntax early and spill the data
    URI into the paragraph as plain text — the same failure by another route.
    """
    return re.sub(r"[\[\]\r\n]+", " ", alt or "").strip() or "Image"


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _timestamp_of(obj: Any, *attrs: str) -> datetime.datetime:
    """First populated timestamp among ``attrs``, else the epoch.

    A sort key must never be ``None`` (unorderable against a datetime) and must
    never mix naive with aware values, so anything missing or naive is
    normalised to UTC. Undated items sort first rather than raising and taking
    the whole record down.
    """
    for attr in attrs:
        value = getattr(obj, attr, None)
        if isinstance(value, datetime.datetime):
            return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    return _EPOCH


def _attributed(label: str, author_login: str | None, created_at: Any, body: str) -> str:
    """Prefix a comment body with its author (and date when known).

    The group *name* already says "Comment by X", but only the group ``data``
    is parsed into blocks and indexed — without this prefix a query like
    "who suggested …" has no author text to ground on.
    """
    who = author_login or "unknown"
    when = (
        f" on {created_at.strftime('%Y-%m-%d')}"
        if isinstance(created_at, datetime.datetime)
        else ""
    )
    return f"**{label} by {who}{when}:**\n\n{body}"


def _by_created_at(items: Sequence[Any], *attrs: str) -> list[Any]:
    """Stable oldest-first ordering; ties keep GitHub's own order."""
    return sorted(items, key=lambda item: _timestamp_of(item, *(attrs or ("created_at",))))


def _is_github_attachment_url(url: str, *, image_only: bool = False) -> bool:
    """True for a ``https://github.com/user-attachments/...`` URL.

    Restricting to this host/path prevents treating arbitrary external links
    embedded in a description as attachments to fetch.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != _GITHUB_ATTACHMENT_HOST:
            return False
        prefix = _GITHUB_ATTACHMENT_ASSET_PREFIX if image_only else _GITHUB_ATTACHMENT_PATH_PREFIX
        return parsed.path.startswith(prefix)
    except Exception:
        return False


def _file_type_from_url(url: str, filename: str = "") -> str:
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext:
            return ext.replace(".", "")
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext:
        return ext.replace(".", "")
    if "user-attachments/assets" in url:
        return "image"
    if "user-attachments/files" in url:
        return "file"
    return "unknown"


class CommentsHelper:
    """Attachment parsing + comment block building for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Markdown/HTML attachment parsing
    # ------------------------------------------------------------------

    async def clean_github_content(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        """Strip GitHub attachment images/links out of markdown/HTML and return
        ``(cleaned_text, attachments)``. Non-attachment links are left untouched."""
        if not isinstance(text, str) or not text:
            return "", []
        attachments: list[dict[str, Any]] = []

        def html_img_handler(match: re.Match[str]) -> str:
            url = match.group(1)
            if not _is_github_attachment_url(url, image_only=True):
                return match.group(0)
            alt_match = re.search(r'alt=["\'](.*?)["\']', match.group(0))
            attachments.append({
                "type": GitHubLiterals.IMAGE.value,
                "href": url,
                "alt": alt_match.group(1) if alt_match else None,
                "slot": len(attachments),
            })
            return _image_placeholder(len(attachments) - 1)

        text = re.sub(
            r'<img\s+[^>]*?src=["\'](.*?)["\'][^>]*?/?>', html_img_handler, text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def md_image_handler(match: re.Match[str]) -> str:
            alt_text, url = match.group(1), match.group(2)
            if not _is_github_attachment_url(url, image_only=True):
                return match.group(0)
            attachments.append({
                "type": GitHubLiterals.IMAGE.value,
                "href": url,
                "alt": alt_text or None,
                "slot": len(attachments),
            })
            return _image_placeholder(len(attachments) - 1)

        text = re.sub(r"!\[(.*?)\]\((.*?)\)", md_image_handler, text)

        def md_link_handler(match: re.Match[str]) -> str:
            link_text, url = match.group(1), match.group(2)
            if not _is_github_attachment_url(url):
                return match.group(0)
            attachments.append({
                "type": _file_type_from_url(url, link_text),
                "href": url,
                "filename": link_text,
            })
            return ""

        text = re.sub(r"\[(.*?)\]\((.*?)\)", md_link_handler, text)

        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text, attachments

    # ------------------------------------------------------------------
    # Image embedding
    # ------------------------------------------------------------------

    async def embed_images_as_base64(self, body_content: str) -> str:
        """Substitute each image back into the body as an inline base64 data URI.

        Substituted **in place**, at the position the author put it: appending
        every image after the prose reorders the content and divorces each
        screenshot from the sentence describing it.

        Fetching here rather than leaving the URL for the indexing pipeline is
        deliberate — ``user-attachments`` assets on private repos need the
        connector's token, which the pipeline does not have.
        """
        if not body_content:
            return ""
        cleaned_text, attachments = await self.clean_github_content(body_content)
        c = self.c
        for attach in attachments:
            slot = attach.get("slot")
            if slot is None:
                continue
            token = f"{_IMG_TOKEN_OPEN}{slot}{_IMG_TOKEN_CLOSE}"
            alt = _sanitize_alt_text(attach.get("alt"))
            url = attach.get("href")
            replacement = alt  # every path must consume the token, or it leaks into the index
            if url and _is_github_attachment_url(url, image_only=True):
                try:
                    res = await c.runtime.ds_call(c.data_source.get_img_bytes, url)
                    if not res.success or not res.data:
                        self.logger.debug(
                            "Failed to fetch image %s: %s", url, getattr(res, "error", "unknown")
                        )
                    elif len(res.data) > _MAX_IMAGE_BYTES:
                        self.logger.debug(
                            "Skipping image %s: exceeds %s bytes", url, _MAX_IMAGE_BYTES
                        )
                    else:
                        image_bytes = res.data
                        fmt = _image_format_from_bytes(image_bytes) or _EXTENSION_TO_MIME.get(
                            _file_type_from_url(url), "png"
                        )
                        base64_data = base64.b64encode(image_bytes).decode("utf-8")
                        replacement = f"![{alt}](data:image/{fmt};base64,{base64_data})"
                except Exception as e:
                    self.logger.warning("Error embedding image from %s: %s", url, e)
            cleaned_text = cleaned_text.replace(token, replacement)
        return cleaned_text

    # ------------------------------------------------------------------
    # Attachment content streaming
    # ------------------------------------------------------------------

    async def fetch_attachment_content(self, record: FileRecord) -> AsyncGenerator[bytes, None]:
        """Stream raw bytes for a FILE attachment record at stream time.

        ``record.external_record_id`` holds the authenticated
        ``user-attachments`` URL captured when the attachment was first seen;
        it is stable (not derived from owner/repo) so it survives repo renames.
        ``weburl`` is NOT used here — it points at the parent issue/PR page,
        which is what a user should open, not what content comes from.

        This path bypasses ``ds_call`` (which expects a response envelope,
        not a generator) and so does not get its retry-on-401. ``stream_record``
        calls ``refresh_token_if_needed`` before dispatching, which covers it.
        """
        c = self.c
        source_url = record.external_record_id
        if not source_url:
            raise Exception(f"No source URL on attachment record {record.id}")
        try:
            async for chunk in c.data_source.get_attachment_files_content(
                source_url, max_bytes=ATTACHMENT_MAX_SIZE_BYTES,
            ):
                yield chunk
        except ValueError as e:
            # The data source raises ValueError only for the size ceiling. 413
            # classifies TERMINAL at the indexing consumer, so the record fails
            # once with a clear reason instead of a transient retry storm.
            raise HTTPException(
                status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
                detail=f"Attachment for record {record.id} is over the size limit: {e}",
            ) from e
        except Exception as e:
            raise Exception(
                f"Failed to fetch attachment content for record {record.id}: {e}"
            ) from e

    # ------------------------------------------------------------------
    # File record creation
    # ------------------------------------------------------------------

    def _attachments_indexing_enabled(self, parent: Record) -> bool:
        """Attachments follow their parent's indexing filter — there is no
        separate attachments filter. Stamped here, at the single construction
        point, because comment attachments are only discovered at stream time
        (listings carry no comment bodies) and that path used to skip the
        filter entirely: with manual indexing on, indexing an issue silently
        auto-queued every PDF in its comments.
        """
        parent_type = getattr(parent, "record_type", None)
        parent_type = getattr(parent_type, "value", parent_type)
        if parent_type == RecordType.TICKET.value:
            return self.c.issues._issues_indexing_enabled()
        if parent_type == RecordType.PULL_REQUEST.value:
            return self.c.pull_requests._prs_indexing_enabled()
        return True

    def _attachment_file_update(
        self, attach: dict[str, Any], record: Record, existing_record: Record | None,
        parent_node_id: str | None,
    ) -> RecordUpdate | None:
        """Build one attachment FileRecord update. No DB access.

        ``existing_record`` is only consulted for ``source_created_at``: the
        processor re-runs the same external-id lookup, overwrites ``record.id``
        with the stored one, and derives everything else itself — but it does
        NOT carry ``source_created_at`` forward, so without the caller's lookup
        every re-sync would reset an attachment's created time to "now".

        ``parent_node_id`` must be the parent ticket/PR's TRUE internal id —
        chat citation enrichment reads ``isDependentNode``/``parentNodeId``
        off the record doc (not the PARENT_CHILD edge) to annotate a cited
        attachment with its parent's context. The processor does not resolve
        it, so a wrong id here dangles silently.
        """
        if attach.get("type") == GitHubLiterals.IMAGE.value:
            return None
        attachment_url = attach.get("href")
        if not attachment_url:
            return None
        attachment_name = attach.get("filename") or os.path.basename(urlparse(attachment_url).path) or "attachment"
        attachment_type = attach.get("type") or "unknown"
        filerecord = FileRecord(
            id=str(uuid.uuid4()),
            org_id=self.c.data_entities_processor.org_id,
            record_name=attachment_name,
            record_type=RecordType.FILE.value,
            external_record_id=str(attachment_url),
            connector_name=self.c.connector_name,
            connector_id=self.c.connector_id,
            origin=OriginTypes.CONNECTOR,
            # The issue/PR page, not the raw user-attachments URL: weburl is the
            # user-facing "open at source" link, and the attachment URL is a bare
            # download. The source URL still lives in external_record_id (same
            # value), which is what content streaming reads.
            weburl=getattr(record, "weburl", None) or str(attachment_url),
            record_group_type=RecordGroupType.PROJECT.value,
            parent_external_record_id=record.external_record_id,
            parent_record_type=record.record_type,
            external_record_group_id=record.external_record_group_id,
            mime_type=getattr(MimeTypes, attachment_type.upper(), MimeTypes.UNKNOWN).value,
            extension=attachment_type.lower(),
            is_file=True,
            inherit_permissions=True,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            version=0,
            size_in_bytes=0,
            source_created_at=(
                getattr(existing_record, "source_created_at", None) or get_epoch_timestamp_in_ms()
            ),
            source_updated_at=get_epoch_timestamp_in_ms(),
        )
        if not self._attachments_indexing_enabled(record):
            filerecord.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return RecordUpdate(
            record=filerecord, is_new=existing_record is None, is_updated=existing_record is not None,
            is_deleted=False, metadata_changed=False, content_changed=False, permissions_changed=False,
            old_permissions=[], new_permissions=[], external_record_id=str(attachment_url),
        )

    async def make_file_records_from_list(
        self, attachments: list[dict[str, Any]], record: Record
    ) -> list[RecordUpdate]:
        """Build FileRecord RecordUpdates for non-image attachments.

        One lookup per attachment (to preserve ``source_created_at``), plus one
        per call for the parent's true node id: ``record`` here is freshly
        built at sync time, and for an issue that already exists the processor
        discards its uuid in favour of the stored id — so ``parent_node_id``
        must come from the DB when the parent is already there.
        """
        c = self.c
        list_records_new: list[RecordUpdate] = []
        parent_node_id: str | None = None
        for attach in attachments:
            attachment_url = attach.get("href")
            if attach.get("type") == GitHubLiterals.IMAGE.value or not attachment_url:
                continue
            if parent_node_id is None:
                existing_parent = await c.data_entities_processor.get_record_by_external_id(
                    c.connector_id, str(record.external_record_id),
                )
                parent_node_id = existing_parent.id if existing_parent else record.id
            existing_record = await c.data_entities_processor.get_record_by_external_id(
                c.connector_id, str(attachment_url),
            )
            record_update = self._attachment_file_update(attach, record, existing_record, parent_node_id)
            if record_update is not None:
                list_records_new.append(record_update)
        return list_records_new

    async def make_child_records_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[ChildRecord], list[RecordUpdate]]:
        """Build ChildRecord list and remaining file RecordUpdates from markdown body."""
        c = self.c
        _, attachments = await self.clean_github_content(markdown_raw)
        child_records: list[ChildRecord] = []
        remaining: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("type") == GitHubLiterals.IMAGE.value:
                continue
            attachment_url = attach.get("href")
            existing_record = await c.data_entities_processor.get_record_by_external_id(
                c.connector_id, str(attachment_url),
            )
            if existing_record:
                child_records.append(ChildRecord(
                    child_id=existing_record.id, child_type=ChildType.RECORD, child_name=existing_record.record_name,
                ))
            else:
                # record came from the DB (stream time), so its id is the true
                # parent node id — no lookup needed.
                new_record = self._attachment_file_update(attach, record, None, record.id)
                if new_record is not None:
                    remaining.append(new_record)
                    child_records.append(ChildRecord(
                        child_id=new_record.record.id, child_type=ChildType.RECORD,
                        child_name=new_record.record.record_name,
                    ))
        return child_records, remaining

    async def make_block_comment_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[CommentAttachment], list[RecordUpdate]]:
        """Build CommentAttachment list and remaining file RecordUpdates for PR review comments."""
        c = self.c
        _, attachments = await self.clean_github_content(markdown_raw)
        comment_attachments: list[CommentAttachment] = []
        remaining: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("type") == GitHubLiterals.IMAGE.value:
                continue
            attachment_url = attach.get("href")
            existing_record = await c.data_entities_processor.get_record_by_external_id(
                c.connector_id, str(attachment_url),
            )
            if existing_record:
                comment_attachments.append(CommentAttachment(name=existing_record.record_name, id=existing_record.id))
            else:
                new_record = self._attachment_file_update(attach, record, None, record.id)
                if new_record is not None:
                    remaining.append(new_record)
                    comment_attachments.append(CommentAttachment(
                        name=new_record.record.record_name, id=new_record.record.id,
                    ))
        return comment_attachments, remaining

    # ------------------------------------------------------------------
    # Issue comment blocks
    # ------------------------------------------------------------------

    async def build_issue_comment_blocks(
        self, owner: str, repo: str, issue_number: int, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build one BlockGroup per issue comment."""
        c = self.c
        comments_res = await c.runtime.ds_call(c.data_source.list_issue_comments, owner, repo, issue_number)
        if not comments_res.success:
            raise Exception(f"Failed to fetch comments for issue #{issue_number}: {comments_res.error}")

        block_groups: list[BlockGroup] = []
        remaining: list[RecordUpdate] = []
        block_group_number = parent_index + 1
        # GitHub issue comments carry no reply/parent field — the thread is a
        # flat list, so chronological order is the only structure to preserve.
        for comment in _by_created_at(comments_res.data or []):
            raw_body: str = getattr(comment, "body", "") or ""
            child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record)
            remaining.extend(new_remaining)
            body_with_images = await self.embed_images_as_base64(raw_body)
            author = getattr(comment, "user", None)
            author_login = getattr(author, "login", None) if author else None
            name = f"Comment by {author_login} on issue #{issue_number}" if author_login else f"Comment on issue #{issue_number}"
            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=name,
                type=GroupType.TEXT_SECTION.value,
                format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value,
                source_group_id=str(getattr(comment, "id", "")),
                data=_attributed(
                    "Comment", author_login, getattr(comment, "created_at", None), body_with_images,
                ),
                weburl=getattr(comment, "html_url", None),
                source_modified_date=getattr(comment, "updated_at", None),
                requires_processing=True,
                children_records=child_records,
            )
            block_groups.append(bg)
            block_group_number += 1
        return block_groups, remaining

    # ------------------------------------------------------------------
    # PR comments + file-diff blocks
    # ------------------------------------------------------------------

    async def build_pr_comment_and_diff_blocks(
        self, owner: str, repo: str, pr_number: int, pull_request: Any, parent_index: int, record: Record,
        start_index: int | None = None,
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build conversation-comment BlockGroups, then one TEXT_SECTION BlockGroup
        per changed file with that file's review comments attached.

        Review comments are grouped by real GitHub review thread
        (``in_reply_to_id`` chain), matching ``BlockGroup.comments``'s documented
        "grouped by thread_id" contract. Grouping every comment on a path into
        one thread, or each into its own, both misrepresent the conversation.

        ``parent_index`` is the *semantic* parent group (the PR description,
        ``bg_0``) that every produced group points back to via its own
        ``parent_index`` field — it is independent of numbering. ``start_index``
        is the first free ``index`` to hand out; it defaults to
        ``parent_index + 1`` (the historical behaviour, valid only when no
        other groups have already been allocated indices above the parent),
        but callers that insert other groups (e.g. a commits section) between
        the parent and these groups must pass the next free index explicitly
        to avoid colliding with those groups.
        """
        c = self.c
        block_groups: list[BlockGroup] = []
        remaining: list[RecordUpdate] = []
        block_group_number = start_index if start_index is not None else parent_index + 1

        conversation_res = await c.runtime.ds_call(c.data_source.list_issue_comments, owner, repo, pr_number)
        if not conversation_res.success:
            raise Exception(f"Failed to fetch conversation comments for PR #{pr_number}: {conversation_res.error}")
        reviews_res = await c.runtime.ds_call(c.data_source.get_pull_reviews, owner, repo, pr_number)

        # Conversation comments and review bodies come from two endpoints but are
        # one conversation. Emitting all of one before the other puts a reply
        # ahead of the review it answers, so merge on time before assigning indices.
        timeline: list[tuple[datetime.datetime, str, Any]] = [
            (_timestamp_of(comment, "created_at"), "Comment", comment)
            for comment in (conversation_res.data or [])
        ]
        if reviews_res.success:
            timeline.extend(
                (_timestamp_of(review, "submitted_at", "created_at"), "Review", review)
                for review in (reviews_res.data or [])
                # A bodyless review is just an approve/reject state change; its
                # substance is in the inline comments attached to the diffs below.
                if (getattr(review, "body", "") or "").strip()
            )
        timeline.sort(key=lambda entry: entry[0])

        for _, label, item in timeline:
            raw_body: str = getattr(item, "body", "") or ""
            child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record)
            remaining.extend(new_remaining)
            body_with_images = await self.embed_images_as_base64(raw_body)
            author = getattr(item, "user", None)
            author_login = getattr(author, "login", None) if author else None
            name = (
                f"{label} by {author_login} on pull request #{pr_number}"
                if author_login else f"{label} on pull request #{pr_number}"
            )
            bg = BlockGroup(
                index=block_group_number, parent_index=parent_index, name=name,
                type=GroupType.TEXT_SECTION.value, format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value, source_group_id=str(getattr(item, "id", "")),
                data=_attributed(
                    label, author_login,
                    getattr(item, "created_at", None) or getattr(item, "submitted_at", None),
                    body_with_images,
                ),
                weburl=getattr(item, "html_url", None),
                source_modified_date=getattr(item, "updated_at", None),
                requires_processing=True, children_records=child_records,
            )
            block_groups.append(bg)
            block_group_number += 1

        if not reviews_res.success:
            self.logger.warning("Failed to fetch reviews for PR #%s: %s", pr_number, reviews_res.error)

        # path -> thread root id -> that thread's comments, in arrival order.
        review_comments_map: dict[str, dict[Any, list[BlockComment]]] = {}
        # path -> markdown section per comment, same order. `comments` above is
        # UI metadata — only group `data` is parsed into blocks and indexed, so
        # without these sections inline review text is unsearchable.
        review_comment_sections: dict[str, list[str]] = {}
        rc_res = await c.runtime.ds_call(c.data_source.get_pull_review_comments, owner, repo, pr_number)
        if rc_res.success:
            # Sorting the flat listing up front puts each thread's replies in
            # order and seeds threads in root order, since dicts keep insertion order.
            for rc in _by_created_at(rc_res.data or []):
                # Guard first: building attachments below persists FileRecords
                # via `remaining`, so discarding the comment afterwards would
                # leave them orphaned with no block referencing them.
                path = getattr(rc, "path", None)
                if not path:
                    continue
                raw_body = getattr(rc, "body", "") or ""
                comment_attachments, new_remaining = await self.make_block_comment_of_attachments(raw_body, record)
                remaining.extend(new_remaining)
                body_with_images = await self.embed_images_as_base64(raw_body)
                author = getattr(rc, "user", None)
                thread_root = getattr(rc, "in_reply_to_id", None) or getattr(rc, "id", None)
                block_comment = BlockComment(
                    text=body_with_images,
                    format=DataFormat.MARKDOWN,
                    weburl=getattr(rc, "html_url", None),
                    updated_at=getattr(rc, "updated_at", None),
                    created_at=getattr(rc, "created_at", None),
                    attachments=comment_attachments,
                    author_id=str(getattr(author, "id", "")) or None if author else None,
                    author_name=getattr(author, "login", None) if author else None,
                    thread_id=str(thread_root) if thread_root is not None else None,
                )
                review_comments_map.setdefault(path, {}).setdefault(thread_root, []).append(block_comment)

                # Blank-line separation keeps any embedded image block-level
                # (an inline data URI in a TEXT block fails validation).
                line_no = getattr(rc, "line", None) or getattr(rc, "original_line", None)
                is_reply = bool(getattr(rc, "in_reply_to_id", None))
                section_label = (
                    "Reply" if is_reply
                    else f"Review comment (line {line_no})" if line_no
                    else "Review comment"
                )
                review_comment_sections.setdefault(path, []).append(
                    _attributed(
                        section_label,
                        getattr(author, "login", None) if author else None,
                        getattr(rc, "created_at", None),
                        body_with_images,
                    )
                )
        else:
            self.logger.warning("Failed to fetch review comments for PR #%s: %s", pr_number, rc_res.error)

        files_res = await c.runtime.ds_call(
            c.data_source.get_pull_file_changes, owner, repo, pr_number, False,
        )
        if not files_res.success:
            raise Exception(f"Failed to fetch file changes for PR #{pr_number}: {files_res.error}")

        head_sha = getattr(pull_request, "head", None)
        head_sha = getattr(head_sha, "sha", None) if head_sha else None
        changes_url = f"{getattr(pull_request, 'html_url', '')}/files"

        for file in files_res.data or []:
            filename = getattr(file, "filename", None)
            if not filename:
                continue
            status = getattr(file, "status", "")
            patch = getattr(file, "patch", None) or "(no textual diff available — binary or too large)"
            file_content: str | None = None
            oversized = False
            content_bytes: int | None = None
            if status != "removed" and head_sha:
                content_res = await c.runtime.ds_call(
                    c.data_source.get_file_contents, owner, repo, filename, head_sha,
                )
                if content_res.success and content_res.data is not None:
                    file_content, oversized, content_bytes = self._inline_file_content(
                        content_res.data,
                    )

            data_parts = [f"[{status} file]"]
            if oversized:
                data_parts.append(
                    f"Full File Content: omitted ({content_bytes} bytes exceeds the "
                    f"{PR_FILE_INLINE_CONTENT_MAX_BYTES}-byte inline limit)"
                )
            elif file_content:
                data_parts.append(f"Full File Content:\n{file_content}")
            data_parts.append(f"Diff:\n{patch}")
            sections = review_comment_sections.get(filename)
            if sections:
                data_parts.append("Review comments on this file:")
                data_parts.extend(sections)

            threads = review_comments_map.get(filename, {})
            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=f"File change: {filename}",
                type=GroupType.TEXT_SECTION,
                format=DataFormat.MARKDOWN,
                sub_type=GroupSubType.PR_FILE_CHANGE,
                data="\n\n".join(data_parts),
                comments=list(threads.values()),
                weburl=changes_url,
                requires_processing=True,
            )
            block_groups.append(bg)
            block_group_number += 1

        return block_groups, remaining

    def _inline_file_content(
        self, content_file: Any,
    ) -> tuple[str | None, bool, int | None]:
        """Embed decoded PR-file text only when it is under the byte cap.

        Prefer the Contents API ``size`` (bytes) so a huge blob is never
        decoded. Listing ``changes`` is a line count and is not consulted.
        """
        declared = getattr(content_file, "size", None)
        if isinstance(declared, int) and declared > PR_FILE_INLINE_CONTENT_MAX_BYTES:
            return None, True, declared
        decoded = self._decode_content_file(content_file)
        if decoded is None:
            return None, False, declared if isinstance(declared, int) else None
        size = len(decoded.encode("utf-8"))
        if size > PR_FILE_INLINE_CONTENT_MAX_BYTES:
            return None, True, size
        return decoded, False, size

    def _decode_content_file(self, content_file: Any) -> str | None:
        """Decoded file text, or ``None`` when it could not be decoded.

        Returns ``None`` rather than ``""`` so callers can distinguish a
        genuinely empty file from a decode failure and omit the section instead
        of emitting an empty one.
        """
        try:
            decoded = getattr(content_file, "decoded_content", None)
            if decoded is not None:
                return decoded.decode("utf-8", errors="replace")
            raw = getattr(content_file, "content", None)
            if raw:
                return base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception as e:
            self.logger.warning(
                "Could not decode GitHub file content for %s: %s",
                getattr(content_file, "path", "<unknown>"), e,
            )
            return None
        return None

    # ------------------------------------------------------------------
    # Commit blocks (PR commits section)
    # ------------------------------------------------------------------

    async def build_pr_commit_blocks(
        self, owner: str, repo: str, pr_number: int, index: int, parent_index: int,
    ) -> tuple[list[Block], BlockGroup | None]:
        """Build one TEXT/COMMIT Block per commit in the PR, plus the owning COMMITS BlockGroup.

        ``index`` is the slot this group occupies; ``parent_index`` is the group
        it hangs under. The two were previously one argument, which left the
        commits group with no parent at all — a second root beside the
        description rather than a section of it.
        """
        c = self.c
        commits_res = await c.runtime.ds_call(c.data_source.get_pull_commits, owner, repo, pr_number)
        if not commits_res.success:
            self.logger.warning("Failed to fetch commits for PR #%s: %s", pr_number, commits_res.error)
            return [], None

        blocks: list[Block] = []
        for i, commit in enumerate(commits_res.data or []):
            git_commit = getattr(commit, "commit", None)
            message = getattr(git_commit, "message", "") if git_commit else ""
            committer = getattr(git_commit, "committer", None) if git_commit else None
            date = getattr(committer, "date", None) if committer else None
            blocks.append(Block(
                index=i,
                parent_index=index,
                type=BlockType.TEXT.value,
                sub_type=BlockSubType.COMMIT.value,
                format=DataFormat.MARKDOWN,
                data=message,
                weburl=getattr(commit, "html_url", None),
                source_id=getattr(commit, "sha", None),
                source_creation_date=date,
            ))
        bg = BlockGroup(
            index=index,
            parent_index=parent_index,
            name="Commits",
            type=GroupType.COMMITS,
            description=f"List of commits for pull request #{pr_number}",
        )
        return blocks, bg
