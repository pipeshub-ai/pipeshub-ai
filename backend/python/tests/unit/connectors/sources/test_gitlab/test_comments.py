"""Unit tests for gitlab CommentsHelper.

Covers:
- build_comment_blocks: success, API failure, system vs regular comments
- build_merge_request_comment_blocks: review comments, system note labeling,
  file change blocks, API failures
"""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.gitlab.comments import CommentsHelper
from app.connectors.sources.gitlab.constants import MR_FILE_INLINE_CONTENT_MAX_BYTES

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
        c.runtime.ds_call = AsyncMock(
            return_value=failed_res("forbidden", status_code=HttpStatusCode.FORBIDDEN.value)
        )
        record = _make_record()

        with pytest.raises(HTTPException) as exc_info:
            await helper.build_comment_blocks(
                "https://gitlab.com/ns/proj/-/issues/7", 0, record
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

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
        c.runtime.ds_call = AsyncMock(
            return_value=failed_res("error", status_code=HttpStatusCode.UNAUTHORIZED.value)
        )
        record = _make_record()

        # A revoked token must not read as "deleted" — 409 tells the user to reconnect.
        with pytest.raises(HTTPException) as exc_info:
            await helper.build_merge_request_comment_blocks(
                "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
            )
        assert exc_info.value.status_code == HttpStatusCode.CONFLICT.value

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
        changes_fail = failed_res("forbidden", status_code=HttpStatusCode.FORBIDDEN.value)
        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_fail])
        record = _make_record()

        with pytest.raises(HTTPException) as exc_info:
            await helper.build_merge_request_comment_blocks(
                "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
            )
        assert exc_info.value.status_code == HttpStatusCode.FORBIDDEN.value

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

    async def test_file_with_a_diff_is_built_without_a_content_fetch(self) -> None:
        """A file with a diff is never fetched, so a broken contents API cannot drop it."""
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
        content_fail = MagicMock(success=False, data=None, error="not found")

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, content_fail])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        assert len(blocks) == 1
        assert c.runtime.ds_call.await_count == 2

    async def test_undecodable_content_is_not_inlined(self) -> None:
        """Content that is not valid base64 is left out, not inlined as raw text."""
        c, helper = self._make_mr_helper()
        file_change = {
            "new_path": "src/a.py",
            "diff": "",
            "new_file": False,
            "deleted_file": False,
            "generated_file": False,
            "too_large": True,
        }
        notes_res = _ok_res([])
        changes_res = _ok_res({"changes": [file_change]})
        mr_res = _ok_res(MagicMock(sha="abc"))

        # Return invalid base64 content
        file_obj = MagicMock()
        file_obj.content = "not-valid-base64!!!"
        file_obj.size = 19
        content_res = _ok_res(file_obj)

        c.runtime.ds_call = AsyncMock(side_effect=[notes_res, changes_res, mr_res, content_res])
        record = _make_record()

        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, record
        )
        # Block should still be created even with bad base64
        assert len(blocks) == 1
        assert "not-valid-base64" not in blocks[0].data

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


# ===========================================================================
# build_merge_request_comment_blocks — what a changed file's group holds
# ===========================================================================


def _change(path: str = "src/a.py", diff: str = "@@ -1 +1 @@\n-a\n+b", **flags: object) -> dict:
    return {
        "new_path": path,
        "old_path": flags.pop("old_path", path),
        "diff": diff,
        "new_file": False,
        "deleted_file": False,
        "renamed_file": False,
        "generated_file": False,
        "too_large": False,
        "a_mode": "100644",
        "b_mode": "100644",
        **flags,
    }


def _file(text: bytes, size: int | None = None) -> MagicMock:
    file_obj = MagicMock()
    file_obj.content = base64.b64encode(text).decode()
    file_obj.size = len(text) if size is None else size
    return file_obj


class TestMrFileChangeContent:
    """A changed file's group holds its diff, not the whole file.

    The file is fetched only when GitLab gives no diff for it, since its text
    at the merge request's head is then the only view of the change.
    """

    def _run(self, changes: list[dict], files: dict[str, object] | None = None,
             payload: dict | None = None,
             mr_sha: str | None = "abc") -> tuple[MagicMock, CommentsHelper, list[tuple[str, dict]]]:
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="text")
        c.attachments.make_block_comment_of_attachments = AsyncMock(return_value=([], []))
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        ds = c.data_source
        calls: list[tuple[str, dict]] = []
        files = files or {}

        async def ds_call(fn: object, **kwargs: object) -> MagicMock:
            if fn is ds.list_merge_request_notes:
                calls.append(("notes", kwargs))
                return _ok_res([])
            if fn is ds.list_merge_request_changes:
                calls.append(("changes", kwargs))
                return _ok_res({**(payload or {}), "changes": changes})
            if fn is ds.get_merge_request:
                calls.append(("mr", kwargs))
                return _ok_res(MagicMock(sha=mr_sha))
            if fn is ds.get_file_content:
                calls.append(("file", kwargs))
                found = files.get(kwargs["file_path"])
                if isinstance(found, MagicMock):
                    return _ok_res(found)
                return MagicMock(success=False, data=None, error="404 File Not Found")
            raise AssertionError(f"unexpected call {fn}")

        c.runtime.ds_call = AsyncMock(side_effect=ds_call)
        return c, CommentsHelper(c), calls

    async def _groups(self, helper: CommentsHelper) -> dict[str, str]:
        blocks, _ = await helper.build_merge_request_comment_blocks(
            "https://gitlab.com/ns/proj/-/merge_requests/1", 0, _make_record()
        )
        return {b.name.removeprefix("block for file "): b.data for b in blocks}

    async def test_file_with_a_diff_is_not_fetched_or_inlined(self) -> None:
        _, helper, calls = self._run([_change("src/a.py")], files={"src/a.py": _file(b"whole file")})

        groups = await self._groups(helper)

        assert groups["src/a.py"] == "Existing file\n\nDiff content\n\n@@ -1 +1 @@\n-a\n+b"
        assert [name for name, _ in calls] == ["notes", "changes"]

    async def test_new_file_with_a_diff_is_not_fetched(self) -> None:
        _, helper, calls = self._run([_change("src/new.py", diff="@@ -0,0 +1 @@\n+x", new_file=True, a_mode="0")])

        groups = await self._groups(helper)

        assert groups["src/new.py"] == "[New file]\n\nDiff content\n\n@@ -0,0 +1 @@\n+x"
        assert "file" not in [name for name, _ in calls]

    async def test_one_content_call_per_file_without_a_diff(self) -> None:
        changes = [_change("a.py"), _change("b.py"), _change("c.py"),
                   _change("package-lock.json", diff="", too_large=True)]
        _, helper, calls = self._run(changes, files={"package-lock.json": _file(b'{"lockfileVersion": 3}')})

        groups = await self._groups(helper)

        assert [(name, kw.get("file_path")) for name, kw in calls if name in ("mr", "file")] == [
            ("mr", None), ("file", "package-lock.json"),
        ]
        assert next(kw for name, kw in calls if name == "file")["ref"] == "abc"
        assert groups["package-lock.json"] == (
            'Existing file\n\nFull file content:\n\n{"lockfileVersion": 3}\n\nDiff content\n\n\n\n[TRUNCATED] Diff'
        )
        assert all("Full file content" not in groups[p] for p in ("a.py", "b.py", "c.py"))

    async def test_head_sha_from_the_changes_payload_skips_the_merge_request_fetch(self) -> None:
        _, helper, calls = self._run(
            [_change("big.py", diff="", too_large=True)], files={"big.py": _file(b"x = 1")},
            payload={"sha": "def"},
        )

        await self._groups(helper)

        assert [name for name, _ in calls] == ["notes", "changes", "file"]
        assert calls[-1][1]["ref"] == "def"

    async def test_merge_request_is_fetched_once_for_several_files_without_a_diff(self) -> None:
        changes = [_change(p, diff="", too_large=True) for p in ("a.py", "b.py")]
        _, helper, calls = self._run(changes, files={"a.py": _file(b"a"), "b.py": _file(b"b")})

        await self._groups(helper)

        assert [name for name, _ in calls] == ["notes", "changes", "mr", "file", "file"]

    async def test_collapsed_or_empty_diff_is_fetched_and_inlined(self) -> None:
        changes = [_change("collapsed.py", diff="", collapsed=True), _change("empty.py", diff="")]
        _, helper, _ = self._run(changes, files={"collapsed.py": _file(b"c = 1"), "empty.py": _file(b"e = 1")})

        groups = await self._groups(helper)

        assert "Full file content:\n\nc = 1" in groups["collapsed.py"]
        assert "Full file content:\n\ne = 1" in groups["empty.py"]

    async def test_deleted_file_without_a_diff_is_not_fetched(self) -> None:
        _, helper, calls = self._run([_change("gone.py", diff="", deleted_file=True, too_large=True, b_mode="0")])

        groups = await self._groups(helper)

        assert "file" not in [name for name, _ in calls]
        assert groups["gone.py"].startswith("[Deleted file]")

    async def test_pure_rename_is_not_fetched_and_names_its_old_path(self) -> None:
        _, helper, calls = self._run([_change("src/new_name.py", diff="", renamed_file=True, old_path="src/old.py")])

        groups = await self._groups(helper)

        assert groups["src/new_name.py"] == "Existing file\n\nRenamed from src/old.py."
        assert "file" not in [name for name, _ in calls]

    async def test_rename_with_a_diff_names_its_old_path_beside_the_diff(self) -> None:
        _, helper, _ = self._run([_change("b.py", renamed_file=True, old_path="a.py")])

        groups = await self._groups(helper)

        assert groups["b.py"] == "Existing file\n\nRenamed from a.py.\n\nDiff content\n\n@@ -1 +1 @@\n-a\n+b"

    async def test_too_large_rename_is_still_fetched(self) -> None:
        _, helper, calls = self._run(
            [_change("b.py", diff="", renamed_file=True, old_path="a.py", too_large=True)],
            files={"b.py": _file(b"b = 2")},
        )

        groups = await self._groups(helper)

        assert [kw["file_path"] for name, kw in calls if name == "file"] == ["b.py"]
        assert "Renamed from a.py." in groups["b.py"] and "b = 2" in groups["b.py"]

    async def test_mode_change_without_a_diff_is_not_fetched(self) -> None:
        _, helper, calls = self._run([_change("run.sh", diff="", b_mode="100755")])

        groups = await self._groups(helper)

        assert groups["run.sh"] == "Existing file\n\nFile mode changed from 100644 to 100755."
        assert "file" not in [name for name, _ in calls]

    async def test_new_empty_file_is_not_fetched(self) -> None:
        _, helper, calls = self._run([_change("__init__.py", diff="", new_file=True, a_mode="0")])

        groups = await self._groups(helper)

        assert groups["__init__.py"] == "[New file]"
        assert "file" not in [name for name, _ in calls]

    async def test_binary_file_without_a_diff_is_not_inlined(self) -> None:
        changes = [_change("logo.png", diff="", too_large=True), _change("blob.bin", diff="", too_large=True)]
        files = {"logo.png": _file(b"\x89PNG\r\n\x1a\n\xff\xfe"), "blob.bin": _file(b"text\x00more")}
        _, helper, _ = self._run(changes, files=files)

        groups = await self._groups(helper)

        for path in ("logo.png", "blob.bin"):
            assert "Full file content" not in groups[path]
            assert files[path].content not in groups[path]

    async def test_file_over_the_limit_is_omitted_with_a_note(self) -> None:
        limit = MR_FILE_INLINE_CONTENT_MAX_BYTES
        changes = [_change("declared.json", diff="", too_large=True), _change("decoded.json", diff="", too_large=True)]
        files = {
            "declared.json": _file(b"{}", size=limit + 1),
            "decoded.json": _file(b"x" * (limit + 5), size=None),
        }
        files["decoded.json"].size = None
        _, helper, _ = self._run(changes, files=files)

        groups = await self._groups(helper)

        assert f"Full file content: omitted ({limit + 1} bytes exceeds the {limit}-byte limit)" in groups["declared.json"]
        assert f"Full file content: omitted ({limit + 5} bytes exceeds the {limit}-byte limit)" in groups["decoded.json"]
        assert "xxxx" not in groups["decoded.json"]

    async def test_failed_content_fetch_keeps_the_file_group(self) -> None:
        _, helper, _ = self._run([_change("big.py", diff="", too_large=True)], files={})

        groups = await self._groups(helper)

        assert groups["big.py"] == "Existing file\n\nDiff content\n\n\n\n[TRUNCATED] Diff"
