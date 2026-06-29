"""Unit tests for gitlab CommentsHelper.

Covers:
- build_comment_blocks: success, API failure, system vs regular comments
- build_merge_request_comment_blocks: review comments, system note labeling,
  file change blocks, API failures
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.comments import CommentsHelper

from .conftest import make_mock_connector, failed_res

pytestmark = pytest.mark.anyio


def _make_record(project_id: str = "42") -> MagicMock:
    r = MagicMock()
    r.id = "rec-1"
    r.record_name = "Test Issue"
    r.external_record_group_id = f"{project_id}-work-items"
    r.weburl = "https://gitlab.com/ns/proj/-/issues/7"
    return r


def _make_note(body: str = "A comment", username: str = "alice", system: bool = False) -> MagicMock:
    note = MagicMock()
    note.body = body
    note.author = {"username": username}
    note.system = system
    note.position = None
    note.updated_at = "2024-01-01T00:00:00Z"
    note.created_at = "2024-01-01T00:00:00Z"
    return note


def _make_review_note(body: str = "Review", file_path: str = "src/a.py") -> MagicMock:
    note = MagicMock()
    note.body = body
    note.system = False
    note.position = {"new_path": file_path}
    note.updated_at = "2024-01-01T00:00:00Z"
    note.created_at = "2024-01-01T00:00:00Z"
    return note


def _ok_res(data: object) -> MagicMock:
    res = MagicMock()
    res.success = True
    res.data = data
    res.error = None
    return res


# ===========================================================================
# build_comment_blocks
# ===========================================================================


class TestBuildCommentBlocks:
    def _make_comments_helper(self) -> tuple[MagicMock, CommentsHelper]:
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="text")
        helper = CommentsHelper(c)
        return c, helper

    async def test_empty_notes_returns_empty_blocks(self) -> None:
        c, helper = self._make_comments_helper()
        c.runtime.ds_call = AsyncMock(return_value=_ok_res([]))
        record = _make_record()

        blocks, remaining = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/issues/7", 0, record
        )
        assert blocks == []
        assert remaining == []

    async def test_single_note_creates_one_block_group(self) -> None:
        c, helper = self._make_comments_helper()
        note = _make_note("Hello!")
        c.runtime.ds_call = AsyncMock(return_value=_ok_res([note]))
        record = _make_record()

        blocks, _ = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/issues/7", 0, record
        )
        assert len(blocks) == 1
        assert "alice" in blocks[0].name

    async def test_api_failure_raises_exception(self) -> None:
        c, helper = self._make_comments_helper()
        c.runtime.ds_call = AsyncMock(return_value=failed_res("forbidden"))
        record = _make_record()

        with pytest.raises(Exception, match="Failed to fetch comments"):
            await helper.build_comment_blocks(
                "https://gitlab.com/ns/proj/-/issues/7", 0, record
            )

    async def test_multiple_notes_creates_multiple_block_groups(self) -> None:
        c, helper = self._make_comments_helper()
        notes = [_make_note(f"Comment {i}", f"user{i}") for i in range(3)]
        c.runtime.ds_call = AsyncMock(return_value=_ok_res(notes))
        record = _make_record()

        blocks, _ = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/issues/7", 0, record
        )
        assert len(blocks) == 3

    async def test_work_items_url_parses_correctly(self) -> None:
        """work_items URL format (newer GitLab) must parse the same as classic issues URL."""
        c, helper = self._make_comments_helper()
        note = _make_note("via work_items URL")
        c.runtime.ds_call = AsyncMock(return_value=_ok_res([note]))
        record = _make_record()

        blocks, _ = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/work_items/7", 0, record
        )
        assert len(blocks) == 1


# ===========================================================================
# build_merge_request_comment_blocks
# ===========================================================================


class TestBuildMergeRequestCommentBlocks:
    def _make_mr_helper(self) -> tuple[MagicMock, CommentsHelper]:
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="text")
        c.attachments.make_block_comment_of_attachments = AsyncMock(return_value=([], []))
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        helper = CommentsHelper(c)
        return c, helper

    def _make_file_change(self, path: str = "src/a.py", is_new: bool = False) -> dict:
        return {
            "new_path": path,
            "diff": "@@diff@@",
            "new_file": is_new,
            "deleted_file": False,
            "generated_file": False,
            "too_large": False,
        }

    async def test_mr_api_failure_raises(self) -> None:
        c, helper = self._make_mr_helper()
        c.runtime.ds_call = AsyncMock(return_value=failed_res("error"))
        record = _make_record()

        with pytest.raises(Exception, match="Failed to fetch comments for merge request"):
            await helper.build_merge_request_comment_blocks(
                "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
            )

    async def test_system_comment_includes_system_label(self) -> None:
        c, helper = self._make_mr_helper()
        sys_note = _make_note("merged", "gitlab", system=True)
        file_changes_res = _ok_res({"changes": []})
        mr_res = _ok_res(MagicMock(sha="abc"))

        c.runtime.ds_call = AsyncMock(
            side_effect=[_ok_res([sys_note]), file_changes_res, mr_res]
        )
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert any("System" in b.name for b in blocks)

    async def test_file_change_block_created_for_each_file(self) -> None:
        c, helper = self._make_mr_helper()
        file_changes_res = _ok_res({"changes": [self._make_file_change("a.py"), self._make_file_change("b.py")]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        file_content_res = _ok_res(MagicMock(content=""))

        c.runtime.ds_call = AsyncMock(
            side_effect=[
                _ok_res([]),  # notes
                file_changes_res,
                mr_res,
                file_content_res,  # file content for a.py
                file_content_res,  # file content for b.py
            ]
        )
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 2

    async def test_review_comment_not_added_as_standalone_block(self) -> None:
        c, helper = self._make_mr_helper()
        review = _make_review_note("inline comment", "src/a.py")
        file_changes_res = _ok_res({"changes": []})
        mr_res = _ok_res(MagicMock(sha="abc"))

        c.runtime.ds_call = AsyncMock(
            side_effect=[_ok_res([review]), file_changes_res, mr_res]
        )
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        # Review comments are attached to file blocks, not standalone
        assert all("Review" not in b.name for b in blocks)


# ===========================================================================
# build_comment_blocks — username fallback
# ===========================================================================


class TestCommentUsernameAndFallback:
    def _make_comments_helper(self):
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="text")
        return c, CommentsHelper(c)

    async def test_username_present_in_comment_name(self) -> None:
        """Comment with username → name includes username."""
        c, helper = self._make_comments_helper()
        note = _make_note("Hello", username="bob")
        c.runtime.ds_call = AsyncMock(return_value=_ok_res([note]))
        record = _make_record()

        blocks, _ = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/issues/7", 0, record
        )
        assert "bob" in blocks[0].name

    async def test_missing_username_uses_fallback_name(self) -> None:
        """Comment without username → generic name used (no crash)."""
        c, helper = self._make_comments_helper()
        note = _make_note("Anonymous comment", username="")
        note.author = {}  # No username key
        c.runtime.ds_call = AsyncMock(return_value=_ok_res([note]))
        record = _make_record()

        blocks, _ = await helper.build_comment_blocks(
            "https://gitlab.com/ns/proj/-/issues/7", 0, record
        )
        assert len(blocks) == 1
        assert "Comment on issue" in blocks[0].name


# ===========================================================================
# build_merge_request_comment_blocks — additional paths
# ===========================================================================


class TestMrCommentAdditionalPaths:
    def _make_mr_helper(self):
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="text")
        c.attachments.make_block_comment_of_attachments = AsyncMock(return_value=([], []))
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        return c, CommentsHelper(c)

    async def test_list_merge_request_changes_failure_raises(self) -> None:
        """When list_merge_request_changes fails, exception is raised."""
        c, helper = self._make_mr_helper()
        notes_res = _ok_res([])  # empty notes
        changes_fail = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_fail])
        record = _make_record()

        with pytest.raises(Exception, match="Failed to fetch file changes"):
            await helper.build_merge_request_comment_blocks(
                "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
            )

    async def test_non_review_comment_no_username_system_naming(self) -> None:
        """System comment without username uses 'System Comment on merge request' name."""
        c, helper = self._make_mr_helper()
        sys_note = MagicMock()
        sys_note.body = "auto merged"
        sys_note.system = True
        sys_note.position = None
        sys_note.author = {}  # no username
        sys_note.updated_at = "2024-01-01T00:00:00Z"
        sys_note.created_at = "2024-01-01T00:00:00Z"

        file_changes_res = _ok_res({"changes": []})
        mr_res = _ok_res(MagicMock(sha="abc"))
        c.runtime.ds_call = AsyncMock(side_effect=[_ok_res([sys_note]), file_changes_res, mr_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert any("System Comment on merge request" in b.name for b in blocks)

    async def test_file_content_fetch_failure_skips_file(self) -> None:
        """When get_file_content fails for a file change, that file is skipped (continue)."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/a.py",
            "diff": "@@diff@@",
            "new_file": False,
            "deleted_file": False,
            "generated_file": False,
            "too_large": False,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        content_fail = MagicMock(success=False, data=None, error="not found")

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_fail])
        record = _make_record()

        # Should not raise; file is skipped
        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 0

    async def test_base64_decode_failure_uses_raw_content(self) -> None:
        """Invalid base64 content falls back to raw content string."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/a.py",
            "diff": "@@diff@@",
            "new_file": False,
            "deleted_file": False,
            "generated_file": False,
            "too_large": False,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))

        # Return invalid base64 content
        file_obj = MagicMock()
        file_obj.content = "not-valid-base64!!!"
        content_res = _ok_res(file_obj)

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        # Block should still be created even with bad base64
        assert len(blocks) == 1

    async def test_deleted_file_label_in_block_data(self) -> None:
        """Deleted file produces '[Deleted file]' label in block data."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/removed.py",
            "diff": "@@diff@@",
            "new_file": False,
            "deleted_file": True,
            "generated_file": False,
            "too_large": False,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        # Deleted file: no get_file_content call (is_new_file=False and is_deleted_file=True)
        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 1
        assert "[Deleted file]" in blocks[0].data

    async def test_truncated_diff_appends_label(self) -> None:
        """too_large=True appends [TRUNCATED] Diff to data."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/big.py",
            "diff": "@@diff@@",
            "new_file": False,
            "deleted_file": False,
            "generated_file": False,
            "too_large": True,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        file_obj = MagicMock()
        file_obj.content = ""
        content_res = _ok_res(file_obj)

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 1
        assert "[TRUNCATED]" in blocks[0].data

    async def test_generated_file_label_in_block_data(self) -> None:
        """generated_file=True produces '[Generated file]' label."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/generated.py",
            "diff": "@@diff@@",
            "new_file": False,
            "deleted_file": False,
            "generated_file": True,
            "too_large": False,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        file_obj = MagicMock()
        file_obj.content = ""
        content_res = _ok_res(file_obj)

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 1
        assert "[Generated file]" in blocks[0].data

    async def test_new_file_label_in_block_data(self) -> None:
        """new_file=True produces '[New file]' label."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/newfile.py",
            "diff": "@@diff@@",
            "new_file": True,
            "deleted_file": False,
            "generated_file": False,
            "too_large": False,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))
        file_obj = MagicMock()
        file_obj.content = ""
        content_res = _ok_res(file_obj)

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 1
        assert "[New file]" in blocks[0].data
