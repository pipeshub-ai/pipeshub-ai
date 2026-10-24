"""
Comment block building for the GitLab connector.

Responsibilities:
- ``build_comment_blocks``: build BlockGroups from issue notes.
- ``build_merge_request_comment_blocks``: build BlockGroups from MR notes + file-change diffs.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.error.stream_errors import raise_for_stream_fetch
from app.models.entities import Record
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.code_parser.secret_scan import redact_secrets
from app.utils.time_conversion import string_to_datetime

from .common.utils import parse_item_id_from_url
from .constants import MR_FILE_INLINE_CONTENT_MAX_BYTES
from .models import GitlabLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class CommentsHelper:
    """Comment block building for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Issue comments
    # ------------------------------------------------------------------

    async def build_comment_blocks(
        self, issue_url: str, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build BlockGroups from an issue's notes."""
        c = self.c
        issue_number = parse_item_id_from_url(issue_url)
        project_id = record.external_record_group_id.split("-")[0]

        comments_res = await c.runtime.ds_call(
            c.data_source.list_issue_notes,
            project_id=int(project_id), issue_iid=issue_number, get_all=True,
        )
        if not comments_res.success:
            raise_for_stream_fetch(
                success=comments_res.success,
                has_payload=bool(comments_res.data),
                connector=c.display_name,
                status=comments_res.status_code,
                message=comments_res.error,
            )

        block_groups: list[BlockGroup] = []
        list_remaining_records: list[RecordUpdate] = []
        block_group_number = parent_index + 1
        comments = comments_res.data or []
        base_project_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"

        for comment in comments:
            raw_markdown_content: str = getattr(comment, "body", "") or ""
            child_records, remaining_records = await c.attachments.make_child_records_of_attachments(
                markdown_raw=raw_markdown_content, record=record
            )
            list_remaining_records.extend(remaining_records)
            markdown_content_with_images_base64 = await c.attachments.embed_images_as_base64(
                raw_markdown_content, base_project_url
            )
            comment_author = getattr(comment, "author", {}) or {}
            comment_username = comment_author.get("username")
            if comment_username:
                comment_name = f"Comment by {comment_username} on issue {issue_number}"
            else:
                comment_name = f"Comment on issue {issue_number}"

            bg = BlockGroup(
                index=block_group_number,
                parent_index=parent_index,
                name=comment_name,
                type=GroupType.TEXT_SECTION.value,
                format=DataFormat.MARKDOWN.value,
                sub_type=GroupSubType.COMMENT.value,
                data=markdown_content_with_images_base64,
                weburl=issue_url,
                requires_processing=True,
                children_records=child_records,
            )
            block_group_number += 1
            block_groups.append(bg)

        return block_groups, list_remaining_records

    # ------------------------------------------------------------------
    # MR comments
    # ------------------------------------------------------------------

    async def build_merge_request_comment_blocks(
        self, mr_url: str, parent_index: int, record: Record
    ) -> tuple[list[BlockGroup], list[RecordUpdate]]:
        """Build BlockGroups from MR notes and per-file code diffs."""
        c = self.c
        mr_number = parse_item_id_from_url(mr_url)
        project_id = record.external_record_group_id.split("-")[0]

        comments_res = await c.runtime.ds_call(
            c.data_source.list_merge_request_notes,
            project_id=int(project_id), mr_iid=mr_number, get_all=True,
        )
        if not comments_res.success:
            raise_for_stream_fetch(
                success=comments_res.success,
                has_payload=bool(comments_res.data),
                connector=c.display_name,
                status=comments_res.status_code,
                message=comments_res.error,
            )

        block_groups: list[BlockGroup] = []
        block_group_number = parent_index + 1
        comments = comments_res.data or []
        list_remaining_attachments: list[RecordUpdate] = []
        map_file_r_comments: dict[str, list[BlockComment]] = {}
        base_project_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"

        for comment in comments:
            is_system_comment = getattr(comment, "system", False)
            is_review_comment = getattr(comment, "position", None)

            if is_review_comment:
                raw_markdown_content: str = getattr(comment, "body", "") or ""
                markdown_content_with_images_base64 = await c.attachments.embed_images_as_base64(
                    raw_markdown_content, base_project_url
                )
                comment_attachments, remaining_attachments = await c.attachments.make_block_comment_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                position = getattr(comment, "position", {})
                file_path = position.get("new_path")
                comment_modified_date = getattr(comment, GitlabLiterals.UPDATED_AT.value, "")
                comment_created_date = getattr(comment, "created_at", "")
                block_comment = BlockComment(
                    text=markdown_content_with_images_base64,
                    format=DataFormat.MARKDOWN.value,
                    updated_at=string_to_datetime(comment_modified_date),
                    created_at=string_to_datetime(comment_created_date),
                    attachments=comment_attachments,
                )
                if file_path:
                    map_file_r_comments.setdefault(file_path, []).append(block_comment)
            else:
                raw_markdown_content = getattr(comment, "body", "") or ""
                markdown_content_with_images_base64 = await c.attachments.embed_images_as_base64(
                    raw_markdown_content, base_project_url
                )
                child_records, remaining_attachments = await c.attachments.make_child_records_of_attachments(
                    markdown_raw=raw_markdown_content, record=record
                )
                list_remaining_attachments.extend(remaining_attachments)
                comment_author = getattr(comment, "author", {})
                comment_username = comment_author.get("username")
                data = markdown_content_with_images_base64

                if comment_username:
                    if is_system_comment:
                        comment_name = f"System Comment by {comment_username} on merge request {mr_number}"
                        data = f"System comment \n\n {markdown_content_with_images_base64}"
                    else:
                        comment_name = f"Comment by {comment_username} on merge request {mr_number}"
                else:
                    if is_system_comment:
                        comment_name = f"System Comment on merge request {mr_number}"
                        data = f"System comment \n\n {markdown_content_with_images_base64}"
                    else:
                        comment_name = f"Comment on merge request {mr_number}"

                comment_modified_date = getattr(comment, GitlabLiterals.UPDATED_AT.value, "")
                source_modified_date = string_to_datetime(comment_modified_date)
                bg = BlockGroup(
                    index=block_group_number,
                    parent_index=parent_index,
                    name=comment_name,
                    type=GroupType.TEXT_SECTION.value,
                    format=DataFormat.MARKDOWN.value,
                    sub_type=GroupSubType.COMMENT.value,
                    data=data,
                    weburl=mr_url,
                    source_modified_date=source_modified_date,
                    requires_processing=True,
                    children_records=child_records,
                )
                block_group_number += 1
                block_groups.append(bg)

        # File diff blocks
        file_changes_res = await c.runtime.ds_call(
            c.data_source.list_merge_request_changes,
            project_id=int(project_id), mr_iid=mr_number,
        )
        if not file_changes_res.success:
            raise_for_stream_fetch(
                success=file_changes_res.success,
                has_payload=bool(file_changes_res.data),
                connector=c.display_name,
                status=file_changes_res.status_code,
                message=file_changes_res.error,
            )

        file_changes = file_changes_res.data or {}
        changes = file_changes.get("changes", [])
        # The changes payload is the merge request itself plus its ``changes``,
        # so its ``sha`` is the head commit. The merge request is fetched for
        # it only if that is missing and a file actually needs its contents.
        head_sha: str | None = file_changes.get("sha") or None
        head_sha_looked_up = head_sha is not None

        for file_change in changes:
            file_path = file_change.get("new_path", "")
            old_path = file_change.get("old_path", "")
            diff_content = file_change.get("diff", "") or ""
            is_new_file = file_change.get("new_file", False)
            is_deleted_file = file_change.get("deleted_file", False)
            is_renamed_file = file_change.get("renamed_file", False)
            is_generated_file = file_change.get("generated_file", False)
            is_truncated_diff = file_change.get("too_large", False)
            is_collapsed_diff = file_change.get("collapsed", False)
            a_mode = file_change.get("a_mode")
            b_mode = file_change.get("b_mode")

            # The diff and review comments describe a change; the whole file
            # at the merge request's head is fetched only when GitLab gives
            # no diff for it (``too_large`` or ``collapsed``, both of which
            # empty the diff, or an empty diff), since it is then the only
            # view of the change. A binary file is not fetched: its diff is
            # GitLab's "Binary files ... differ" notice. With no diff and both
            # flags false, a rename, a mode change or a new empty file changed
            # no text, so fetching it would index the same file once more.
            # GitLab before 18.4 sends neither flag and still empties a diff
            # over its limit, so without them an empty diff is always fetched.
            flags_known = "too_large" in file_change and "collapsed" in file_change
            # GitLab reports mode "0" for the missing side of a new or deleted
            # file, so only a file present on both sides can change mode.
            mode_changed = bool(
                not is_new_file and not is_deleted_file and a_mode and b_mode and a_mode != b_mode
            )
            pruned = is_truncated_diff or is_collapsed_diff or not flags_known
            nothing_to_show = not pruned and (is_renamed_file or mode_changed or is_new_file)
            needs_content = not diff_content and not is_deleted_file and not nothing_to_show

            file_content: str | None = None
            oversized_bytes: int | None = None
            if needs_content:
                if not head_sha_looked_up:
                    head_sha_looked_up = True
                    mr_res = await c.runtime.ds_call(
                        c.data_source.get_merge_request, project_id=int(project_id), mr_iid=mr_number,
                    )
                    head_sha = getattr(mr_res.data, "sha", None) if mr_res.success else None
                    if not mr_res.success:
                        self.logger.warning(
                            "Could not fetch MR %s for its head commit: %s", mr_url, mr_res.error,
                        )
                if head_sha:
                    content_res = await c.runtime.ds_call(
                        c.data_source.get_file_content,
                        project_id=int(project_id), file_path=file_path, ref=head_sha,
                    )
                    if content_res.success and content_res.data:
                        file_content, oversized_bytes = await self._inline_file_content(
                            content_res.data, file_path
                        )
                    else:
                        self.logger.warning(
                            "Could not fetch file content for %s in MR %s: %s",
                            file_path, mr_url, content_res.error,
                        )
                else:
                    self.logger.warning(
                        "No head commit for MR %s; %s has no diff and is indexed without its content",
                        mr_url, file_path,
                    )

            if is_generated_file:
                data_parts = ["[Generated file]"]
            elif is_new_file:
                data_parts = ["[New file]"]
            elif is_deleted_file:
                data_parts = ["[Deleted file]"]
            else:
                data_parts = ["Existing file"]
            # Name the old path, so a search for it still finds the merge
            # request that moved the file.
            if is_renamed_file and old_path and old_path != file_path:
                data_parts.append(f"Renamed from {old_path}.")
            if mode_changed:
                data_parts.append(f"File mode changed from {a_mode} to {b_mode}.")
            if oversized_bytes is not None:
                data_parts.append(
                    f"Full file content: omitted ({oversized_bytes} bytes exceeds the "
                    f"{MR_FILE_INLINE_CONTENT_MAX_BYTES}-byte limit)"
                )
            elif file_content:
                data_parts.append(f"Full file content:\n\n{file_content}")
            if diff_content or not nothing_to_show:
                data_parts.append(f"Diff content\n\n{diff_content}")
            data = "\n\n".join(data_parts)
            if is_truncated_diff:
                data = data + "\n\n[TRUNCATED] Diff"
            data = redact_secrets(data).text

            file_comments = map_file_r_comments.get(file_path, [])
            comments_list = [file_comments] if file_comments else []
            bg_n = BlockGroup(
                index=block_group_number,
                name=f"block for file {file_path}",
                type=GroupType.FULL_CODE_PATCH,
                format=DataFormat.MARKDOWN,
                sub_type=GroupSubType.PR_FILE_CHANGE,
                data=data,
                comments=comments_list,
                requires_processing=True,
            )
            block_groups.append(bg_n)
            block_group_number += 1

        return block_groups, list_remaining_attachments

    async def _inline_file_content(self, file_obj: object, file_path: str) -> tuple[str | None, int | None]:
        """Text of a fetched file to inline, and its size when it is too big.

        Returns ``(text, None)`` for text within ``MR_FILE_INLINE_CONTENT_MAX_BYTES``,
        ``(None, size)`` when it is larger, and ``(None, None)`` for a file that
        is not text (it does not decode as UTF-8, or holds a NUL byte, git's own
        test for binary), so its bytes are never indexed as text.
        """
        declared = getattr(file_obj, "size", None)
        if isinstance(declared, int) and declared > MR_FILE_INLINE_CONTENT_MAX_BYTES:
            return None, declared
        raw = getattr(file_obj, "content", "") or ""
        try:
            decoded = (await asyncio.to_thread(base64.b64decode, raw)).decode(GitlabLiterals.UTF_8.value)
        except Exception as e:
            self.logger.warning("Not inlining %s: its content is not UTF-8 text (%s)", file_path, e)
            return None, None
        if "\x00" in decoded:
            return None, None
        size = len(decoded.encode(GitlabLiterals.UTF_8.value))
        if size > MR_FILE_INLINE_CONTENT_MAX_BYTES:
            return None, size
        return decoded, None
