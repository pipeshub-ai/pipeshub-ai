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
from app.connectors.core.base.error.stream_errors import (
    not_downloadable,
    raise_for_stream_fetch,
)
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
from app.modules.parsers.code_parser.secret_scan import redact_secrets
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .constants import (
    ATTACHMENT_MAX_SIZE_BYTES,
    PR_DIFF_TEXT_MAX_BYTES,
    PR_FILE_INLINE_CONTENT_MAX_BYTES,
    PR_FILE_INLINE_MAX_FILES,
)
from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

_MAX_IMAGE_BYTES = 5 * 1024 * 1024

_GITHUB_ATTACHMENT_HOST = "github.com"
_GITHUB_ATTACHMENT_PATH_PREFIX = "/user-attachments/"
_STORED_PAGE = 500
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


def _reuse_trashed_id(update: RecordUpdate, trashed: Record | None) -> None:
    """An attachment linked again after it was removed is in the trash (with the trash
    on). Written again under its own id, the processor brings it back and indexes it,
    and the parent's blocks point at that id."""
    if trashed is not None:  # a live one is reused as it is, never rewritten
        update.record.id = trashed.id


def attachment_key(url: str, parent_external_record_id: str) -> str:
    """External id of an attachment: the upload URL *and* the issue or PR linking it.

    The same upload is often linked from several issues, in several repositories.
    Keyed by URL alone, all of them shared one record whose parent and record group
    were whichever issue was written last, so it changed hands, and access, between
    repositories on every sync.
    """
    return f"{url}#{parent_external_record_id}"


def attachment_url(external_record_id: str) -> str:
    """The upload URL inside an attachment's external id (old records are the URL alone)."""
    return external_record_id.split("#", 1)[0]


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

        Fetching here rather than leaving the URL for the indexing pipeline keeps
        the image in place in the text the pipeline receives.
        """
        if not body_content:
            return ""
        cleaned_text, attachments = await self.clean_github_content(body_content)
        # Every issue/PR body and comment passes here; images are still tokens,
        # so the scan neither sees base64 nor breaks attachment URLs.
        cleaned_text = redact_secrets(cleaned_text).text
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
                    res = await c.runtime.ds_call(c.data_source.get_img_bytes, url, max_bytes=_MAX_IMAGE_BYTES)
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
        source_url = attachment_url(record.external_record_id or "")
        if not source_url:
            raise not_downloadable(
                "This attachment has no stored source URL.", connector=c.display_name
            )
        try:
            # Anything else (notably httpx.HTTPStatusError) propagates as-is:
            # its status is what the router maps to a real response code.
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
            external_record_id=attachment_key(str(attachment_url), str(record.external_record_id)),
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
            old_permissions=[], new_permissions=[],
            external_record_id=attachment_key(str(attachment_url), str(record.external_record_id)),
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
                c.connector_id, attachment_key(str(attachment_url), str(record.external_record_id)),
            )
            record_update = self._attachment_file_update(attach, record, existing_record, parent_node_id)
            if record_update is not None:
                list_records_new.append(record_update)
        return list_records_new

    async def make_child_records_of_attachments(
        self, markdown_raw: str, record: Record, seen_keys: set[str] | None = None,
    ) -> tuple[list[ChildRecord], list[RecordUpdate]]:
        """Build ChildRecord list and remaining file RecordUpdates from markdown body.

        Every attachment key met is added to ``seen_keys`` when one is given.
        """
        c = self.c
        _, attachments = await self.clean_github_content(markdown_raw)
        child_records: list[ChildRecord] = []
        remaining: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("type") == GitHubLiterals.IMAGE.value or not attach.get("href"):
                continue
            key = attachment_key(str(attach.get("href")), str(record.external_record_id))
            if seen_keys is not None:
                seen_keys.add(key)
            existing_record = await c.data_entities_processor.get_record_by_external_id(c.connector_id, key)
            if existing_record and existing_record.is_deleted is not True:
                child_records.append(ChildRecord(
                    child_id=existing_record.id, child_type=ChildType.RECORD, child_name=existing_record.record_name,
                ))
            else:
                # record came from the DB (stream time), so its id is the true
                # parent node id — no lookup needed.
                new_record = self._attachment_file_update(attach, record, None, record.id)
                if new_record is not None:
                    _reuse_trashed_id(new_record, existing_record)
                    remaining.append(new_record)
                    child_records.append(ChildRecord(
                        child_id=new_record.record.id, child_type=ChildType.RECORD,
                        child_name=new_record.record.record_name,
                    ))
        return child_records, remaining

    async def make_block_comment_of_attachments(
        self, markdown_raw: str, record: Record, seen_keys: set[str] | None = None,
    ) -> tuple[list[CommentAttachment], list[RecordUpdate]]:
        """Build CommentAttachment list and remaining file RecordUpdates for PR review comments."""
        c = self.c
        _, attachments = await self.clean_github_content(markdown_raw)
        comment_attachments: list[CommentAttachment] = []
        remaining: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("type") == GitHubLiterals.IMAGE.value or not attach.get("href"):
                continue
            key = attachment_key(str(attach.get("href")), str(record.external_record_id))
            if seen_keys is not None:
                seen_keys.add(key)
            existing_record = await c.data_entities_processor.get_record_by_external_id(c.connector_id, key)
            if existing_record and existing_record.is_deleted is not True:
                comment_attachments.append(CommentAttachment(name=existing_record.record_name, id=existing_record.id))
            else:
                new_record = self._attachment_file_update(attach, record, None, record.id)
                if new_record is not None:
                    _reuse_trashed_id(new_record, existing_record)
                    remaining.append(new_record)
                    comment_attachments.append(CommentAttachment(
                        name=new_record.record.record_name, id=new_record.record.id,
                    ))
        return comment_attachments, remaining

    async def settle_stored_attachments(self, external_group_id: str) -> None:
        """Bring a group's stored attachment records in line on a full sync or the first
        sync of this version.

        Files linked from comments and reviews become records only when their issue or
        pull request is streamed; no listing writes them again. A full sync unlinks what
        it did not write, so they are noted as kept (an issue that left GitHub takes its
        attachments with it through the deleted-issue check). Records keyed by the URL
        alone, written before attachments were keyed per parent, are removed: no version
        writes that id any more; their parents are streamed again to write the keyed ones.
        """
        c = self.c
        processor = c.data_entities_processor
        kept: list[Record] = []
        legacy: list[Record] = []
        by_external_id: dict[str, Record] = {}
        after: str | None = None
        while True:
            page = await processor.get_records_in_record_group(
                c.connector_id, external_group_id, _STORED_PAGE, after
            )
            for r in page:
                key = str(r.external_record_id or "")
                by_external_id[key] = r
                if r.record_type == RecordType.FILE.value and _is_github_attachment_url(key.split("#", 1)[0]):
                    (kept if "#" in key else legacy).append(r)
            if len(page) < _STORED_PAGE:
                break
            after = page[-1].id
        processor.note_kept_records(c.connector_id, [r.id for r in kept])
        if not legacy:
            return
        # Their parents are streamed once more, which writes the keyed records (the ones
        # linked from comments and reviews come from no listing). Queued before the
        # removal, so a failure leaves both for the next sync.
        parent_ids = dict.fromkeys(r.parent_external_record_id for r in legacy if r.parent_external_record_id)
        parents = [by_external_id[p] for p in parent_ids if p in by_external_id]
        await processor.reindex_existing_records([p for p in parents if type(p) is not Record])
        await c.record_remover.remove_records(legacy)
        self.logger.info("Replaced %s attachment record(s) keyed by URL alone in %s; re-reading %s parent(s)",
                         len(legacy), external_group_id, len(parents))

    async def remove_unlinked_attachments(self, record: Record, linked_keys: set[str]) -> None:
        """Remove the attachment records of ``record`` that nothing links any more.

        Only called after the record's body and every comment were read in full, so
        ``linked_keys`` is the complete set. Records keyed by URL alone (written
        before attachments were keyed per parent) are replaced by the same stream
        and removed here too.
        """
        c = self.c
        children = await c.data_entities_processor.get_records_by_parent(
            c.connector_id, str(record.external_record_id)
        )
        unlinked = [
            r for r in children
            if r.record_type == RecordType.FILE.value
            and str(r.external_record_id or "").startswith("https://")
            and r.external_record_id not in linked_keys
        ]
        if unlinked:
            await c.record_remover.remove_records(unlinked)

    # ------------------------------------------------------------------
    # Issue comment blocks
    # ------------------------------------------------------------------

    async def build_issue_comment_blocks(
        self, owner: str, repo: str, issue_number: int, parent_index: int, record: Record,
        seen_keys: set[str] | None = None,
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build one BlockGroup per issue comment."""
        c = self.c
        comments_res = await c.runtime.ds_call(c.data_source.list_issue_comments, owner, repo, issue_number)
        if not comments_res.success:
            raise_for_stream_fetch(
                success=comments_res.success,
                has_payload=bool(comments_res.data),
                connector=c.display_name,
                status=comments_res.status_code,
                retry_after=comments_res.retry_after_seconds,
                message=comments_res.error,
            )

        block_groups: list[BlockGroup] = []
        remaining: list[RecordUpdate] = []
        block_group_number = parent_index + 1
        # GitHub issue comments carry no reply/parent field — the thread is a
        # flat list, so chronological order is the only structure to preserve.
        for comment in _by_created_at(comments_res.data or []):
            raw_body: str = getattr(comment, "body", "") or ""
            child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record, seen_keys)
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
        start_index: int | None = None, seen_keys: set[str] | None = None,
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build conversation-comment BlockGroups, then one TEXT_SECTION BlockGroup
        per changed file with that file's review comments attached.

        A file's group holds its diff, not the whole file (with "Index Code
        Files" on, the default branch's copy is its own code-file record). Only a file
        that changed but that GitHub returns without a diff (too large, or
        binary) and that still exists has its text at the PR head inlined,
        within ``PR_FILE_INLINE_CONTENT_MAX_BYTES``. A rename or copy names
        its source path. All diff and inlined text together stays within
        ``PR_DIFF_TEXT_MAX_BYTES``: a file that doesn't fit in what is left
        keeps its group, status and review comments, with its text replaced by
        a note, and later smaller files still fit.

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
            raise_for_stream_fetch(
                success=conversation_res.success,
                has_payload=bool(conversation_res.data),
                connector=c.display_name,
                status=conversation_res.status_code,
                retry_after=conversation_res.retry_after_seconds,
                message=conversation_res.error,
            )
        reviews_res = await c.runtime.ds_call(c.data_source.get_pull_reviews, owner, repo, pr_number)
        if not reviews_res.success:
            # Review bodies are part of the indexed text; without them the PR would be
            # indexed as complete and, its revision unchanged, never read again.
            raise_for_stream_fetch(
                success=reviews_res.success,
                has_payload=bool(reviews_res.data),
                connector=c.display_name,
                status=reviews_res.status_code,
                retry_after=reviews_res.retry_after_seconds,
                message=reviews_res.error,
            )

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
            child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record, seen_keys)
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

        # path -> thread root id -> that thread's comments, in arrival order.
        review_comments_map: dict[str, dict[Any, list[BlockComment]]] = {}
        # path -> markdown section per comment, same order. `comments` above is
        # UI metadata — only group `data` is parsed into blocks and indexed, so
        # without these sections inline review text is unsearchable.
        review_comment_sections: dict[str, list[str]] = {}
        rc_res = await c.runtime.ds_call(c.data_source.get_pull_review_comments, owner, repo, pr_number)
        if not rc_res.success:
            raise_for_stream_fetch(
                success=rc_res.success,
                has_payload=bool(rc_res.data),
                connector=c.display_name,
                status=rc_res.status_code,
                retry_after=rc_res.retry_after_seconds,
                message=rc_res.error,
            )
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
                comment_attachments, new_remaining = await self.make_block_comment_of_attachments(
                    raw_body, record, seen_keys,
                )
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

        files_res = await c.runtime.ds_call(
            c.data_source.get_pull_file_changes, owner, repo, pr_number, False,
        )
        if not files_res.success:
            raise_for_stream_fetch(
                success=files_res.success,
                has_payload=bool(files_res.data),
                connector=c.display_name,
                status=files_res.status_code,
                retry_after=files_res.retry_after_seconds,
                message=files_res.error,
            )

        head_sha = getattr(pull_request, "head", None)
        head_sha = getattr(head_sha, "sha", None) if head_sha else None
        changes_url = f"{getattr(pull_request, 'html_url', '')}/files"

        changed_files = list(files_res.data or [])
        fetched = 0
        text_left = PR_DIFF_TEXT_MAX_BYTES
        omitted = 0
        over_budget_note = (
            f"this pull request's changes exceed the "
            f"{PR_DIFF_TEXT_MAX_BYTES // (1024 * 1024)} MB of diff text indexed per pull "
            f"request; the full change is at {changes_url}"
        )
        for file in changed_files:
            filename = getattr(file, "filename", None)
            if not filename:
                continue
            status = getattr(file, "status", "")
            diff = getattr(file, "patch", None)
            patch = redact_secrets(diff or "(no textual diff available — binary or too large)").text
            file_content: str | None = None
            oversized = False
            content_bytes: int | None = None
            # The diff and review comments describe a change; the whole file
            # at the PR head is fetched only when the file changed but GitHub
            # gives no diff (too large to show, or binary: GitHub counts a
            # binary file's lines as 0/0), since it is then the only view of it.
            # A rename, copy or mode change with no line changes is not
            # fetched: any text it has is the same file indexed once more.
            line_changes = (getattr(file, "additions", 0) or 0) + (getattr(file, "deletions", 0) or 0)
            changed = line_changes > 0 or status in ("added", "modified")
            # Each fetch is one API call, so a PR of many binary or huge files
            # stops inlining after PR_FILE_INLINE_MAX_FILES of them.
            needs_content = not diff and changed and status != "removed" and head_sha
            beyond_inline_limit = bool(needs_content) and fetched >= PR_FILE_INLINE_MAX_FILES
            # Not fetched when what's left couldn't hold a file at the inline
            # cap, so the call isn't spent on text that may not fit.
            content_over_budget = (
                bool(needs_content) and not beyond_inline_limit
                and text_left < PR_FILE_INLINE_CONTENT_MAX_BYTES
            )
            if needs_content and not beyond_inline_limit and not content_over_budget:
                fetched += 1
                content_res = await c.runtime.ds_call(
                    c.data_source.get_file_contents, owner, repo, filename, head_sha,
                )
                if content_res.success and content_res.data is not None:
                    file_content, oversized, content_bytes = self._inline_file_content(
                        content_res.data,
                    )
            if file_content:
                # Measured after redaction, which can lengthen a short secret.
                file_content = redact_secrets(file_content).text
                content_bytes_used = len(file_content.encode("utf-8"))
                if content_bytes_used > text_left:
                    file_content, content_over_budget = None, True
                else:
                    text_left -= content_bytes_used
            diff_over_budget = False
            if diff:
                diff_bytes = len(patch.encode("utf-8"))
                if diff_bytes > text_left:
                    diff_over_budget = True
                else:
                    text_left -= diff_bytes
            if content_over_budget or diff_over_budget:
                omitted += 1

            data_parts = [f"[{status} file]"]
            # Name the old path whenever GitHub gives one, so a search for it
            # still finds the pull request that moved the file.
            previous = getattr(file, "previous_filename", None)
            if previous and status in ("renamed", "copied"):
                data_parts.append(f"{status.capitalize()} from {previous}.")
            if beyond_inline_limit:
                data_parts.append(
                    f"Full File Content: omitted (the pull request has more than "
                    f"{PR_FILE_INLINE_MAX_FILES} changed files without a diff)"
                )
            elif content_over_budget:
                data_parts.append(f"Full File Content: omitted ({over_budget_note})")
            elif oversized:
                data_parts.append(
                    f"Full File Content: omitted ({content_bytes} bytes exceeds the "
                    f"{PR_FILE_INLINE_CONTENT_MAX_BYTES}-byte inline limit)"
                )
            elif file_content:
                data_parts.append(f"Full File Content:\n{file_content}")
            # No diff and nothing fetched: say only what the listing shows.
            # GitHub counts a binary's lines as 0/0 even when its bytes
            # changed, so "no changes" is never claimed for a rename or copy.
            if not diff and not changed and status == "changed":
                data_parts.append("File mode or type changed.")
            elif diff_over_budget:
                data_parts.append(
                    f"Diff: omitted ({getattr(file, 'additions', 0) or 0} lines added, "
                    f"{getattr(file, 'deletions', 0) or 0} removed; {over_budget_note})"
                )
            elif diff or changed or status not in ("renamed", "copied"):
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

        if omitted:
            self.logger.info(
                "Pull request #%s in %s/%s: left out the text of %s of %s changed files, "
                "past the %s-byte diff limit",
                pr_number, owner, repo, omitted, len(changed_files), PR_DIFF_TEXT_MAX_BYTES,
            )
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
        if "\x00" in decoded:
            # Binary (git's own test: a NUL byte). Decoding it as UTF-8 would
            # index a page of replacement characters.
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
            raise_for_stream_fetch(
                success=commits_res.success,
                has_payload=bool(commits_res.data),
                connector=c.display_name,
                status=commits_res.status_code,
                retry_after=commits_res.retry_after_seconds,
                message=commits_res.error,
            )

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
                data=redact_secrets(message or "").text,
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
