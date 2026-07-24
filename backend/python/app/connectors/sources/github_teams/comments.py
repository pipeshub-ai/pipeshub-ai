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
import os
import re
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.config.constants.arangodb import MimeTypes, OriginTypes
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

from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

_MAX_IMAGE_BYTES = 4 * 1024 * 1024

_GITHUB_ATTACHMENT_HOST = "github.com"
_GITHUB_ATTACHMENT_PATH_PREFIX = "/user-attachments/"
_GITHUB_ATTACHMENT_ASSET_PREFIX = "/user-attachments/assets/"

_EXTENSION_TO_MIME: dict[str, str] = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg",
    "gif": "gif", "webp": "webp", "bmp": "bmp", "svg": "svg+xml",
}


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
            })
            return ""

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
            })
            return ""

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
        """Replace GitHub image attachments with inline base64 data URIs."""
        if not body_content:
            return ""
        cleaned_text, attachments = await self.clean_github_content(body_content)
        c = self.c
        for attach in attachments:
            url = attach.get("href")
            if not url or not _is_github_attachment_url(url, image_only=True):
                continue
            try:
                res = await c.runtime.ds_call_async(c.data_source.get_img_bytes, url)
                if not res.success or not res.data:
                    self.logger.debug("Failed to fetch image %s: %s", url, getattr(res, "error", "unknown"))
                    continue
                image_bytes = res.data
                if len(image_bytes) > _MAX_IMAGE_BYTES:
                    self.logger.debug("Skipping image %s: exceeds %s bytes", url, _MAX_IMAGE_BYTES)
                    continue
                fmt = _EXTENSION_TO_MIME.get(_file_type_from_url(url), "png")
                start = image_bytes.lstrip()
                if start.startswith((b"<?xml", b"<svg")):
                    fmt = "svg+xml"
                base64_data = base64.b64encode(image_bytes).decode("utf-8")
                cleaned_text += f"![Image](data:image/{fmt};base64,{base64_data})"
            except Exception as e:
                self.logger.warning("Error embedding image from %s: %s", url, e)
        return cleaned_text

    # ------------------------------------------------------------------
    # Attachment content streaming
    # ------------------------------------------------------------------

    async def fetch_attachment_content(self, record: FileRecord) -> bytes:
        """Fetch raw bytes for a FILE attachment record at stream time.

        ``record.weburl`` holds the authenticated ``user-attachments`` URL
        captured when the attachment was first seen; it is stable (not
        derived from owner/repo) so it survives repo renames.
        """
        c = self.c
        if not record.weburl:
            raise Exception(f"No source URL on attachment record {record.id}")
        res = await c.runtime.ds_call_async(c.data_source.get_attachment_files_content, record.weburl)
        if not res.success or res.data is None:
            raise Exception(f"Failed to fetch attachment content for record {record.id}: {res.error}")
        return res.data

    # ------------------------------------------------------------------
    # File record creation
    # ------------------------------------------------------------------

    async def make_file_records_from_list(
        self, attachments: list[dict[str, Any]], record: Record
    ) -> list[RecordUpdate]:
        """Build FileRecord RecordUpdates for non-image attachments."""
        c = self.c
        list_records_new: list[RecordUpdate] = []
        for attach in attachments:
            if attach.get("type") == GitHubLiterals.IMAGE.value:
                continue
            attachment_url = attach.get("href")
            attachment_name = attach.get("filename") or os.path.basename(urlparse(attachment_url or "").path) or "attachment"
            attachment_type = attach.get("type") or "unknown"
            if not attachment_url:
                continue
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=str(attachment_url),
                )
            filerecord = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                org_id=c.data_entities_processor.org_id,
                record_name=attachment_name,
                record_type=RecordType.FILE.value,
                external_record_id=str(attachment_url),
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR,
                weburl=str(attachment_url),
                record_group_type=RecordGroupType.PROJECT.value,
                parent_external_record_id=record.external_record_id,
                parent_record_type=record.record_type,
                external_record_group_id=record.external_record_group_id,
                mime_type=getattr(MimeTypes, attachment_type.upper(), MimeTypes.UNKNOWN).value,
                extension=attachment_type.lower(),
                is_file=True,
                inherit_permissions=True,
                preview_renderable=True,
                version=0,
                size_in_bytes=0,
                source_created_at=get_epoch_timestamp_in_ms(),
                source_updated_at=get_epoch_timestamp_in_ms(),
            )
            list_records_new.append(RecordUpdate(
                record=filerecord, is_new=existing_record is None, is_updated=existing_record is not None,
                is_deleted=False, metadata_changed=False, content_changed=False, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=str(attachment_url),
            ))
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
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=str(attachment_url),
                )
            if existing_record:
                child_records.append(ChildRecord(
                    child_id=existing_record.id, child_type=ChildType.RECORD, child_name=existing_record.record_name,
                ))
            else:
                new_records = await self.make_file_records_from_list([attach], record)
                remaining.extend(new_records)
                if new_records:
                    child_records.append(ChildRecord(
                        child_id=new_records[0].record.id, child_type=ChildType.RECORD,
                        child_name=new_records[0].record.record_name,
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
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=str(attachment_url),
                )
            if existing_record:
                comment_attachments.append(CommentAttachment(name=existing_record.record_name, id=existing_record.id))
            else:
                new_records = await self.make_file_records_from_list([attach], record)
                remaining.extend(new_records)
                if new_records:
                    comment_attachments.append(CommentAttachment(
                        name=new_records[0].record.record_name, id=new_records[0].record.id,
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
        for comment in comments_res.data or []:
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
                data=body_with_images,
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
    ) -> tuple[list[BlockGroup], list[Block], list[RecordUpdate]]:
        """Build conversation-comment BlockGroups, then one FULL_CODE_PATCH BlockGroup
        per changed file with that file's review comments attached as a single thread.

        Review comments are grouped **per file path into one thread** (all comments
        for a path share one inner list) rather than one single-comment thread per
        comment — the personal connector's original ``review_comments_map[path].append([bc])``
        created a new 1-comment thread each time, which is the bug this fixes.
        """
        c = self.c
        block_groups: list[BlockGroup] = []
        blocks: list[Block] = []
        remaining: list[RecordUpdate] = []
        block_group_number = parent_index + 1

        conversation_res = await c.runtime.ds_call(c.data_source.list_issue_comments, owner, repo, pr_number)
        if not conversation_res.success:
            raise Exception(f"Failed to fetch conversation comments for PR #{pr_number}: {conversation_res.error}")
        for comment in conversation_res.data or []:
            raw_body: str = getattr(comment, "body", "") or ""
            child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record)
            remaining.extend(new_remaining)
            body_with_images = await self.embed_images_as_base64(raw_body)
            author = getattr(comment, "user", None)
            author_login = getattr(author, "login", None) if author else None
            name = f"Comment by {author_login} on pull request #{pr_number}" if author_login else f"Comment on pull request #{pr_number}"
            bg = BlockGroup(
                index=block_group_number, parent_index=parent_index, name=name,
                type=GroupType.TEXT_SECTION.value, format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value, source_group_id=str(getattr(comment, "id", "")),
                data=body_with_images, weburl=getattr(comment, "html_url", None),
                source_modified_date=getattr(comment, "updated_at", None),
                requires_processing=True, children_records=child_records,
            )
            block_groups.append(bg)
            block_group_number += 1

        reviews_res = await c.runtime.ds_call(c.data_source.get_pull_reviews, owner, repo, pr_number)
        if reviews_res.success:
            for review in reviews_res.data or []:
                raw_body = getattr(review, "body", "") or ""
                if not raw_body.strip():
                    continue
                child_records, new_remaining = await self.make_child_records_of_attachments(raw_body, record)
                remaining.extend(new_remaining)
                body_with_images = await self.embed_images_as_base64(raw_body)
                reviewer = getattr(review, "user", None)
                reviewer_login = getattr(reviewer, "login", None) if reviewer else None
                name = f"Review by {reviewer_login} on pull request #{pr_number}" if reviewer_login else f"Review on pull request #{pr_number}"
                bg = BlockGroup(
                    index=block_group_number, parent_index=parent_index, name=name,
                    type=GroupType.TEXT_SECTION.value, format=DataFormat.MARKDOWN.value,
                    sub_type=GroupSubType.COMMENT.value, source_group_id=str(getattr(review, "id", "")),
                    data=body_with_images, weburl=getattr(review, "html_url", None),
                    requires_processing=True, children_records=child_records,
                )
                block_groups.append(bg)
                block_group_number += 1
        else:
            self.logger.warning("Failed to fetch reviews for PR #%s: %s", pr_number, reviews_res.error)

        review_comments_map: dict[str, list[BlockComment]] = {}
        rc_res = await c.runtime.ds_call(c.data_source.get_pull_review_comments, owner, repo, pr_number)
        if rc_res.success:
            for rc in rc_res.data or []:
                raw_body = getattr(rc, "body", "") or ""
                comment_attachments, new_remaining = await self.make_block_comment_of_attachments(raw_body, record)
                remaining.extend(new_remaining)
                body_with_images = await self.embed_images_as_base64(raw_body)
                path = getattr(rc, "path", None)
                if not path:
                    continue
                block_comment = BlockComment(
                    text=body_with_images,
                    format=DataFormat.MARKDOWN,
                    weburl=getattr(rc, "html_url", None),
                    updated_at=getattr(rc, "updated_at", None),
                    created_at=getattr(rc, "created_at", None),
                    attachments=comment_attachments,
                )
                review_comments_map.setdefault(path, []).append(block_comment)
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
            file_content = ""
            if status != "removed" and head_sha:
                content_res = await c.runtime.ds_call(c.data_source.get_file_contents, owner, repo, filename, head_sha)
                if content_res.success and content_res.data is not None:
                    file_content = self._decode_content_file(content_res.data)

            data_parts = [f"[{status} file]"]
            if file_content:
                data_parts.append(f"Full File Content:\n{file_content}")
            data_parts.append(f"Diff:\n{patch}")

            file_threads = review_comments_map.get(filename, [])
            bg = BlockGroup(
                index=block_group_number,
                name=f"File change: {filename}",
                type=GroupType.FULL_CODE_PATCH,
                format=DataFormat.MARKDOWN,
                sub_type=GroupSubType.PR_FILE_CHANGE,
                data="\n\n".join(data_parts),
                comments=[file_threads] if file_threads else [],
                weburl=changes_url,
                requires_processing=True,
            )
            block_groups.append(bg)
            block_group_number += 1

        return block_groups, blocks, remaining

    @staticmethod
    def _decode_content_file(content_file: Any) -> str:
        try:
            decoded = getattr(content_file, "decoded_content", None)
            if decoded is not None:
                return decoded.decode("utf-8", errors="replace")
            raw = getattr(content_file, "content", None)
            if raw:
                return base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Commit blocks (PR commits section)
    # ------------------------------------------------------------------

    async def build_pr_commit_blocks(
        self, owner: str, repo: str, pr_number: int, parent_index: int,
    ) -> tuple[list[Block], BlockGroup | None]:
        """Build one TEXT/COMMIT Block per commit in the PR, plus the owning COMMITS BlockGroup."""
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
                parent_index=parent_index,
                type=BlockType.TEXT.value,
                sub_type=BlockSubType.COMMIT.value,
                format=DataFormat.MARKDOWN,
                data=message,
                weburl=getattr(commit, "html_url", None),
                source_id=getattr(commit, "sha", None),
                source_creation_date=date,
            ))
        bg = BlockGroup(
            index=parent_index,
            name="Commits",
            type=GroupType.COMMITS,
            description=f"List of commits for pull request #{pr_number}",
        )
        return blocks, bg
