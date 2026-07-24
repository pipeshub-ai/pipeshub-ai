"""Unit tests for github_teams CommentsHelper.

Covers:
- clean_github_content: image vs. file attachment extraction; non-attachment
  links left untouched.
- embed_images_as_base64: dispatches through ds_call_async (httpx-backed),
  never ds_call (which would crash on a coroutine-returning method).
- build_pr_comment_and_diff_blocks: review comments on the same file path are
  grouped into a single 2D comment thread (the personal connector's original
  per-comment-thread bug this module fixes).
- fetch_attachment_content: dispatches through ds_call_async.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connectors.sources.github_teams.comments import CommentsHelper, _is_github_attachment_url
from app.models.entities import FileRecord

from .conftest import make_mock_connector, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestIsGithubAttachmentUrl:
    def test_valid_attachment_url(self) -> None:
        assert _is_github_attachment_url("https://github.com/user-attachments/files/1/x.pdf") is True

    def test_valid_image_asset_url(self) -> None:
        assert _is_github_attachment_url("https://github.com/user-attachments/assets/1", image_only=True) is True

    def test_non_attachment_url_rejected(self) -> None:
        assert _is_github_attachment_url("https://example.com/whatever") is False

    def test_wrong_host_rejected(self) -> None:
        assert _is_github_attachment_url("https://evil.com/user-attachments/files/1/x.pdf") is False


class TestCleanGithubContent:
    async def test_extracts_image_and_leaves_regular_links(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        text = (
            "See screenshot ![shot](https://github.com/user-attachments/assets/42) "
            "and read the [docs](https://example.com/docs)."
        )
        cleaned, attachments = await helper.clean_github_content(text)

        assert len(attachments) == 1
        assert attachments[0]["type"] == "image"
        assert attachments[0]["href"] == "https://github.com/user-attachments/assets/42"
        # Non-attachment markdown link must survive untouched.
        assert "[docs](https://example.com/docs)" in cleaned

    async def test_extracts_file_attachment_link(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        text = "Log file: [crash.log](https://github.com/user-attachments/files/9/crash.log)"
        cleaned, attachments = await helper.clean_github_content(text)

        assert len(attachments) == 1
        assert attachments[0]["type"] == "log"
        assert attachments[0]["filename"] == "crash.log"
        assert "crash.log" not in cleaned or "[crash.log]" not in cleaned

    async def test_empty_text_returns_empty(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        cleaned, attachments = await helper.clean_github_content("")
        assert cleaned == ""
        assert attachments == []


class TestEmbedImagesAsBase64:
    async def test_uses_ds_call_async_not_ds_call(self) -> None:
        """get_img_bytes is an async (httpx-backed) data-source method — it must
        be invoked via ds_call_async, never ds_call."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call_async.return_value = ok_response(b"\x89PNG\r\n\x1a\n" + b"0" * 20)

        text = "![shot](https://github.com/user-attachments/assets/1)"
        result = await helper.embed_images_as_base64(text)

        c.runtime.ds_call_async.assert_awaited_once()
        c.runtime.ds_call.assert_not_awaited()
        assert "data:image/png;base64," in result

    async def test_oversized_image_skipped(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        from app.connectors.sources.github_teams import comments as comments_mod
        c.runtime.ds_call_async.return_value = ok_response(b"0" * (comments_mod._MAX_IMAGE_BYTES + 1))

        text = "![shot](https://github.com/user-attachments/assets/1)"
        result = await helper.embed_images_as_base64(text)

        assert "data:image" not in result


class TestFetchAttachmentContent:
    async def test_uses_ds_call_async(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call_async.return_value = ok_response(b"file-bytes")
        record = FileRecord(
            id="rec-1", org_id="org-1", record_name="x.pdf", record_type="FILE",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="ext-1", is_file=True,
            weburl="https://github.com/user-attachments/files/1/x.pdf",
        )

        data = await helper.fetch_attachment_content(record)

        assert data == b"file-bytes"
        c.runtime.ds_call_async.assert_awaited_once()
        c.runtime.ds_call.assert_not_awaited()

    async def test_raises_when_no_weburl(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        record = FileRecord(
            id="rec-1", org_id="org-1", record_name="x.pdf", record_type="FILE",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="ext-1", is_file=True,
        )
        with pytest.raises(Exception):
            await helper.fetch_attachment_content(record)


class TestReviewCommentThreading:
    async def test_multiple_review_comments_on_same_path_form_one_thread(self) -> None:
        """Regression test for the personal connector's bug: two review comments
        on the same file path must land in ONE inner list (one thread), not two
        separate single-comment "threads"."""
        c = make_mock_connector()
        helper = CommentsHelper(c)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                SimpleNamespace(body="first comment", path="src/main.py", html_url="https://github.com/x/y/pull/1#r1", updated_at=None, created_at=None),
                SimpleNamespace(body="second comment", path="src/main.py", html_url="https://github.com/x/y/pull/1#r2", updated_at=None, created_at=None),
            ]),
            "get_pull_file_changes": ok_response([
                SimpleNamespace(filename="src/main.py", status="modified", patch="@@ diff @@"),
            ]),
        })

        record = FileRecord(
            id="rec-1", org_id="org-1", record_name="PR #1", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="ext-pr-1", is_file=False,
        )
        pull_request = SimpleNamespace(head=SimpleNamespace(sha=None), html_url="https://github.com/acme/widgets/pull/1")

        block_groups, _blocks, _remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        file_change_groups = [bg for bg in block_groups if bg.name == "File change: src/main.py"]
        assert len(file_change_groups) == 1
        comments = file_change_groups[0].comments
        assert len(comments) == 1  # one thread...
        assert len(comments[0]) == 2  # ...containing both comments.

    async def test_review_comments_on_different_paths_stay_separate(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                SimpleNamespace(body="a", path="a.py", html_url="https://github.com/x/y/pull/1#r1", updated_at=None, created_at=None),
                SimpleNamespace(body="b", path="b.py", html_url="https://github.com/x/y/pull/1#r2", updated_at=None, created_at=None),
            ]),
            "get_pull_file_changes": ok_response([
                SimpleNamespace(filename="a.py", status="modified", patch="@@"),
                SimpleNamespace(filename="b.py", status="modified", patch="@@"),
            ]),
        })

        record = FileRecord(
            id="rec-1", org_id="org-1", record_name="PR #1", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="ext-pr-1", is_file=False,
        )
        pull_request = SimpleNamespace(head=SimpleNamespace(sha=None), html_url="https://github.com/acme/widgets/pull/1")

        block_groups, _blocks, _remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        for bg in block_groups:
            if bg.name in ("File change: a.py", "File change: b.py"):
                assert len(bg.comments) == 1
                assert len(bg.comments[0]) == 1


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
