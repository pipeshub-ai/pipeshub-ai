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

from app.models.entities import Record
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.utils.time_conversion import string_to_datetime

from .common.utils import parse_item_id_from_url
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
            raise Exception(f"Failed to fetch comments for issue {issue_url}: {comments_res.error}")

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
            raise Exception(f"Failed to fetch comments for merge request {mr_url}: {comments_res.error}")

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
            raise Exception(f"Failed to fetch file changes for merge request {mr_url}: {file_changes_res.error}")

        tmp_mr_res = await c.runtime.ds_call(
            c.data_source.get_merge_request, project_id=int(project_id), mr_iid=mr_number,
        )
        tmp_mr = tmp_mr_res.data
        tmp_mr_sha = getattr(tmp_mr, "sha", "")
        file_changes = file_changes_res.data or {}
        changes = file_changes.get("changes", [])

        for file_change in changes:
            file_path = file_change.get("new_path", "")
            diff_content = file_change.get("diff", "")
            is_new_file = file_change.get("new_file", False)
            is_deleted_file = file_change.get("deleted_file", False)
            is_generated_file = file_change.get("generated_file", False)
            is_truncated_diff = file_change.get("too_large", False)
            new_file_content = ""

            if is_new_file or not is_deleted_file:
                new_file_content_res = await c.runtime.ds_call(
                    c.data_source.get_file_content,
                    project_id=int(project_id), file_path=file_path, ref=tmp_mr_sha,
                )
                if not new_file_content_res.success:
                    self.logger.error("Failed to fetch new file content for %s in MR %s: %s", file_path, mr_url, new_file_content_res.error)
                    continue
                new_file = new_file_content_res.data
                new_file_content = getattr(new_file, "content", "") if new_file else ""

            try:
                file_content = (
                    await asyncio.to_thread(base64.b64decode, new_file_content)
                ).decode(GitlabLiterals.UTF_8.value)
            except Exception as e:
                self.logger.error("Failed to decode code file content for %s: %s", file_path, e)
                file_content = new_file_content

            if is_generated_file:
                data = f"[Generated file] \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            elif is_new_file:
                data = f"[New file] \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            elif is_deleted_file:
                data = f"[Deleted file] \n\n Diff content \n\n {diff_content}"
            else:
                data = f"Existing file \n\n {file_content} \n\n Diff content \n\n {diff_content}"
            if is_truncated_diff:
                data = data + "\n\n[TRUNCATED] Diff"

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
