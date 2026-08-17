"""Unit tests for github_teams CommentsHelper.

Covers:
- clean_github_content: image vs. file attachment extraction; non-attachment
  links left untouched.
- embed_images_as_base64: dispatches through ds_call (httpx-backed),
  never ds_call (which would crash on a coroutine-returning method).
- build_pr_comment_and_diff_blocks: review comments on the same file path are
  grouped into a single 2D comment thread (the personal connector's original
  per-comment-thread bug this module fixes).
- fetch_attachment_content: dispatches through ds_call.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.comments import CommentsHelper, _is_github_attachment_url
from app.models.entities import FileRecord

from .conftest import failed_response, make_mock_connector, ok_response

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
        assert "[crash.log](https://github.com/user-attachments/files/9/crash.log)" not in cleaned

    async def test_empty_text_returns_empty(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        cleaned, attachments = await helper.clean_github_content("")
        assert cleaned == ""
        assert attachments == []


class TestEmbedImagesAsBase64:
    async def test_fetches_image_via_ds_call(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.return_value = ok_response(b"\x89PNG\r\n\x1a\n" + b"0" * 20)

        text = "![shot](https://github.com/user-attachments/assets/1)"
        result = await helper.embed_images_as_base64(text)

        c.runtime.ds_call.assert_awaited_once()
        assert "data:image/png;base64," in result

    async def test_image_format_sniffed_from_bytes_not_url(self) -> None:
        """user-attachments asset URLs are extension-less, so a URL-based guess
        labels every image png; the bytes are the only reliable source."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 20
        c.runtime.ds_call.return_value = ok_response(jpeg)

        result = await helper.embed_images_as_base64(
            "![shot](https://github.com/user-attachments/assets/1)"
        )

        assert "data:image/jpeg;base64," in result

    async def test_oversized_image_skipped(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        from app.connectors.sources.github_teams import comments as comments_mod
        c.runtime.ds_call.return_value = ok_response(b"0" * (comments_mod._MAX_IMAGE_BYTES + 1))

        text = "![shot](https://github.com/user-attachments/assets/1)"
        result = await helper.embed_images_as_base64(text)

        assert "data:image" not in result


def _parent_ticket() -> FileRecord:
    return FileRecord(
        id="parent-uuid", org_id="org-1", record_name="Issue 2", record_type="TICKET",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id="1/issues/2", external_record_group_id="1-work-items",
        is_file=False, weburl="https://github.com/acme/widgets/issues/2",
    )


_PDF_ATTACHMENT = {
    "type": "file", "filename": "spec.pdf",
    "href": "https://github.com/user-attachments/files/9/spec.pdf",
}


class TestAttachmentDependentNode:
    """Chat citation enrichment reads isDependentNode/parentNodeId off the
    record doc, so attachments must carry them — with the parent's TRUE id."""

    async def test_new_parent_uses_built_uuid(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)

        updates = await helper.make_file_records_from_list([_PDF_ATTACHMENT], _parent_ticket())

        (update,) = updates
        assert update.record.is_dependent_node is True
        assert update.record.parent_node_id == "parent-uuid"
        assert update.record.weburl == "https://github.com/acme/widgets/issues/2"

    async def test_existing_parent_uses_db_id_not_discarded_uuid(self) -> None:
        """For an issue already in the DB the processor discards the freshly
        built uuid, so parent_node_id must come from the stored record."""
        c = make_mock_connector()
        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=[SimpleNamespace(id="db-parent-id"), None],
        )
        helper = CommentsHelper(c)

        updates = await helper.make_file_records_from_list([_PDF_ATTACHMENT], _parent_ticket())

        assert updates[0].record.parent_node_id == "db-parent-id"


def _attachment_record(source_url: str | None = None) -> FileRecord:
    # Streaming reads the source URL from external_record_id; weburl points at
    # the parent issue/PR page and must never be fetched.
    return FileRecord(
        id="rec-1", org_id="org-1", record_name="x.pdf", record_type="FILE",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id=source_url or "", is_file=True,
        weburl="https://github.com/acme/widgets/issues/1",
    )


class TestFetchAttachmentContent:
    async def test_streams_chunks_without_buffering(self) -> None:
        """The content must arrive as multiple chunks: buffering the whole body
        defeats the size ceiling and the eager-first-chunk error surfacing."""
        c = make_mock_connector()
        helper = CommentsHelper(c)

        async def fake_stream(weburl: str, max_bytes: int | None = None):
            yield b"chunk-1"
            yield b"chunk-2"

        c.data_source.get_attachment_files_content = fake_stream
        record = _attachment_record("https://github.com/user-attachments/files/1/x.pdf")

        chunks = [chunk async for chunk in helper.fetch_attachment_content(record)]

        assert chunks == [b"chunk-1", b"chunk-2"]

    async def test_upstream_error_is_wrapped_with_record_context(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)

        async def failing_stream(weburl: str, max_bytes: int | None = None):
            raise ValueError("over the limit")
            yield b""  # pragma: no cover - marks this an async generator

        c.data_source.get_attachment_files_content = failing_stream
        record = _attachment_record("https://github.com/user-attachments/files/1/x.pdf")

        with pytest.raises(Exception, match="rec-1"):
            [chunk async for chunk in helper.fetch_attachment_content(record)]

    async def test_raises_when_no_source_url(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        record = _attachment_record()
        with pytest.raises(Exception):
            [chunk async for chunk in helper.fetch_attachment_content(record)]

    async def test_streams_from_external_id_not_weburl(self) -> None:
        """weburl is the parent issue/PR page for humans; bytes must come from
        the attachment URL stored in external_record_id."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        fetched: list[str] = []

        async def fake_stream(url: str, max_bytes: int | None = None):
            fetched.append(url)
            yield b"x"

        c.data_source.get_attachment_files_content = fake_stream
        record = _attachment_record("https://github.com/user-attachments/files/1/x.pdf")

        [chunk async for chunk in helper.fetch_attachment_content(record)]

        assert fetched == ["https://github.com/user-attachments/files/1/x.pdf"]


def _review_comment(
    *, rc_id: int, path: str, body: str = "c", in_reply_to: int | None = None, login: str = "alice",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rc_id,
        body=body,
        path=path,
        in_reply_to_id=in_reply_to,
        user=SimpleNamespace(id=rc_id * 100, login=login),
        html_url=f"https://github.com/x/y/pull/1#r{rc_id}",
        updated_at=None,
        created_at=None,
    )


def _pr_blocks_fixture() -> tuple[FileRecord, SimpleNamespace]:
    record = FileRecord(
        id="rec-1", org_id="org-1", record_name="PR #1", record_type="PULL_REQUEST",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id="ext-pr-1", is_file=False,
    )
    pull_request = SimpleNamespace(
        head=SimpleNamespace(sha=None), html_url="https://github.com/acme/widgets/pull/1",
    )
    return record, pull_request


def _dt(day: int) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, tzinfo=datetime.timezone.utc)


def _conv_comment(cid: int, body: str, created: datetime.datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid, body=body, created_at=created, updated_at=created,
        user=SimpleNamespace(id=cid, login="alice"),
        html_url="https://github.com/x/y/pull/1",
    )


def _review(rid: int, body: str, submitted: datetime.datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid, body=body, submitted_at=submitted, created_at=None, updated_at=None,
        user=SimpleNamespace(id=rid, login="bob"),
        html_url="https://github.com/x/y/pull/1",
    )


class TestImagePlacement:
    """Images must land where the author put them, block-level.

    Appending them after the prose reorders the content and divorces each
    screenshot from the sentence describing it. Inline (no blank line) the data
    URI stays in the paragraph's TEXT block and the record dies on the
    validator's TEXT_DATA_CONTAINS_BASE64_IMAGE check.
    """

    _PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"0" * 20
    _URL = "https://github.com/user-attachments/assets/1"

    async def test_image_stays_between_the_text_around_it(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.return_value = ok_response(self._PNG)

        result = await helper.embed_images_as_base64(
            f"before text\n\n![shot]({self._URL})\n\nafter text"
        )

        before, _, rest = result.partition("![shot](data:image/png;base64,")
        assert before.strip() == "before text"
        assert rest.endswith("\n\nafter text")

    async def test_image_is_block_level(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.return_value = ok_response(self._PNG)

        result = await helper.embed_images_as_base64(f"prose ![shot]({self._URL}) more")

        assert "\n\n![shot](data:image/png;base64," in result
        assert "prose![" not in result

    async def test_unfetchable_image_degrades_to_alt_text(self) -> None:
        """Every path must consume the placeholder — a leaked sentinel would be
        indexed as gibberish."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.return_value = failed_response("403 forbidden")

        result = await helper.embed_images_as_base64(f"before\n\n![my shot]({self._URL})\n\nafter")

        assert "my shot" in result
        from app.connectors.sources.github_teams import comments as comments_mod

        # Reference the constants: the sentinels are invisible private-use
        # characters, so literals here could silently become no-op assertions.
        assert comments_mod._IMG_TOKEN_OPEN not in result
        assert comments_mod._IMG_TOKEN_CLOSE not in result
        assert "data:image" not in result


class TestBlockGroupParenting:
    async def test_commits_group_hangs_under_the_description(self) -> None:
        """It previously took a single overloaded index argument and ended up
        with no parent at all — a second root beside the description."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_pull_commits": ok_response([
                SimpleNamespace(
                    commit=SimpleNamespace(message="c1", committer=SimpleNamespace(date=None)),
                    html_url="https://github.com/x/y/commit/1", sha="abc",
                ),
            ]),
        })

        blocks, commits_bg = await helper.build_pr_commit_blocks(
            "acme", "widgets", 1, index=1, parent_index=0,
        )

        assert commits_bg.index == 1
        assert commits_bg.parent_index == 0
        assert [b.parent_index for b in blocks] == [1]

    async def test_review_interleaves_with_conversation_comments(self) -> None:
        """Two endpoints, one conversation: emitting all comments then all
        reviews puts a reply ahead of the review it answers."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([
                _conv_comment(1, "before", _dt(1)), _conv_comment(3, "after", _dt(3)),
            ]),
            "get_pull_reviews": ok_response([_review(9, "review", _dt(2))]),
            "get_pull_review_comments": ok_response([]),
            "get_pull_file_changes": ok_response([]),
        })
        record, pull_request = _pr_blocks_fixture()

        block_groups, _ = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record, start_index=1,
        )

        assert [bg.data for bg in block_groups] == ["before", "review", "after"]
        assert {bg.parent_index for bg in block_groups} == {0}

    async def test_issue_comments_are_ordered_oldest_first(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([
                _conv_comment(2, "second", _dt(2)),
                _conv_comment(1, "first", _dt(1)),
                _conv_comment(3, "undated", None),
            ]),
        })
        record, _ = _pr_blocks_fixture()

        block_groups, _ = await helper.build_issue_comment_blocks(
            "acme", "widgets", 1, parent_index=0, record=record,
        )

        # An undated comment must sort first, not raise on None vs datetime.
        assert [bg.data for bg in block_groups] == ["undated", "first", "second"]


class TestReviewCommentThreading:
    async def test_reply_chain_forms_one_thread(self) -> None:
        """A reply (in_reply_to_id -> root) belongs in the root's thread."""
        c = make_mock_connector()
        helper = CommentsHelper(c)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                _review_comment(rc_id=1, path="src/main.py", body="first"),
                _review_comment(rc_id=2, path="src/main.py", body="reply", in_reply_to=1),
            ]),
            "get_pull_file_changes": ok_response([
                SimpleNamespace(filename="src/main.py", status="modified", patch="@@ diff @@"),
            ]),
        })
        record, pull_request = _pr_blocks_fixture()

        block_groups, _remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        file_change_groups = [bg for bg in block_groups if bg.name == "File change: src/main.py"]
        assert len(file_change_groups) == 1
        comments = file_change_groups[0].comments
        assert len(comments) == 1  # one thread...
        assert len(comments[0]) == 2  # ...containing the root and its reply.

    async def test_independent_comments_on_same_path_are_separate_threads(self) -> None:
        """Two unrelated comments on one file are two conversations, not one."""
        c = make_mock_connector()
        helper = CommentsHelper(c)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                _review_comment(rc_id=1, path="src/main.py"),
                _review_comment(rc_id=2, path="src/main.py"),
            ]),
            "get_pull_file_changes": ok_response([
                SimpleNamespace(filename="src/main.py", status="modified", patch="@@"),
            ]),
        })
        record, pull_request = _pr_blocks_fixture()

        block_groups, _remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        group = next(bg for bg in block_groups if bg.name == "File change: src/main.py")
        assert len(group.comments) == 2
        assert all(len(thread) == 1 for thread in group.comments)

    async def test_review_comments_carry_author_identity(self) -> None:
        c = make_mock_connector()
        helper = CommentsHelper(c)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                _review_comment(rc_id=1, path="src/main.py", login="bob"),
            ]),
            "get_pull_file_changes": ok_response([
                SimpleNamespace(filename="src/main.py", status="modified", patch="@@"),
            ]),
        })
        record, pull_request = _pr_blocks_fixture()

        block_groups, _remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        group = next(bg for bg in block_groups if bg.name == "File change: src/main.py")
        comment = group.comments[0][0]
        assert comment.author_name == "bob"
        assert comment.thread_id == "1"

    async def test_comment_without_path_persists_no_orphan_file_records(self) -> None:
        """Attachments must not be built before the path guard — the FileRecords
        they produce get persisted, leaving no block referencing them."""
        c = make_mock_connector()
        helper = CommentsHelper(c)
        helper.make_block_comment_of_attachments = AsyncMock(return_value=([], ["orphan"]))

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_comments": ok_response([]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([
                _review_comment(rc_id=1, path=None, body="![img](x.png)"),
            ]),
            "get_pull_file_changes": ok_response([]),
        })
        record, pull_request = _pr_blocks_fixture()

        _block_groups, remaining = await helper.build_pr_comment_and_diff_blocks(
            "acme", "widgets", 1, pull_request, parent_index=0, record=record,
        )

        helper.make_block_comment_of_attachments.assert_not_awaited()
        assert remaining == []


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
