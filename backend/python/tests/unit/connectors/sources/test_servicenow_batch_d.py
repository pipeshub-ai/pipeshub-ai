"""ServiceNow article content: a revision that changes only with what an article streams, the article as
blocks (title and number, body, wiki body, feedback comments and a feedback summary), inline images
embedded as data: URIs, and attachments streamed in chunks.

The Table API is ``FakeServiceNow``; the stored records are ``FakeRecordStore``.
"""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from servicenow_fakes import (
    FakeServiceNow,
    block_groups,
    file_response,
    migrated_sync_point,
    status_error,
)
from test_servicenow_batch_a import INSTANCE, T0, _article
from test_servicenow_batch_c import EARLIER, KB1, LATER, V1, V2, _wire
from test_servicenow_connector import (  # noqa: F401
    mock_config_service,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_logger,
    servicenow_connector,
)

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.servicenow.servicenow.connector import _FEEDBACK_FIELDS
from app.connectors.sources.servicenow.servicenow.constants import ServiceNowDefaults
from app.models.entities import FileRecord, RecordType
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import TableAPIRecord
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

IMG = "c" * 32  # an inline image's sys_attachment sys_id
PNG = b"\x89PNG\r\n\x1a\n-image-bytes"


def _feedback(n: int, article: str = "a0001", number: str = KB1, **fields: str) -> dict:
    return {
        "sys_id": f"fb{n:02d}", "article": article, "article.number": number, "comments": f"comment {n}",
        "useful": "", "rating": "", "flagged": "false", "parent_comment": "", "root_feedback": "",
        "user.name": f"Reader {n}", "reason": "", "work_notes": "",
        "sys_created_on": f"2024-06-0{n} 00:00:00", "sys_updated_on": f"2024-06-0{n} 00:00:00", **fields,
    }


async def _revision(sc, fake: FakeServiceNow) -> str:
    """The revision a full read writes for KB1."""
    store = _wire(sc, fake)
    await sc._sync_articles()
    return store.records[KB1].external_revision_id


# --- SN-44: the revision is what the article streams ---------------------------------------------


@pytest.mark.asyncio
async def test_title_only_change_keeps_revision(servicenow_connector) -> None:  # noqa: F811
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        _article(1, LATER, short_description="Renamed")]}))

    assert before == after


@pytest.mark.asyncio
async def test_move_keeps_revision(servicenow_connector) -> None:  # noqa: F811
    # The processor refreshes the vectors' groups on a move (syncVectorMembership), so no re-index.
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        _article(1, LATER, kb_category="cat2")]}))

    assert before == after


@pytest.mark.asyncio
async def test_body_change_changes_revision(servicenow_connector) -> None:  # noqa: F811
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        _article(1, LATER, text="<p>edited</p>")]}))

    assert before != after


@pytest.mark.asyncio
async def test_new_version_same_body_keeps_revision(servicenow_connector) -> None:  # noqa: F811
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, EARLIER, sys_id=V1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        _article(1, EARLIER, sys_id=V1, workflow_state="outdated"), _article(1, LATER, sys_id=V2)]}))

    assert before == after


def test_attachment_revision_is_size_and_creation(servicenow_connector) -> None:  # noqa: F811
    from app.sources.external.servicenow.models import AttachmentMetadata

    def build(updated: str) -> FileRecord | None:
        return servicenow_connector._transform_to_attachment_file_record(AttachmentMetadata(
            sys_id="f1", file_name="a.pdf", content_type="application/pdf", size_bytes="10",
            table_sys_id="a0001", sys_created_on="2015-01-01 00:00:00", sys_updated_on=updated))

    assert build("2015-01-01 00:00:00").external_revision_id == build("2026-01-01 00:00:00").external_revision_id


# --- SN-45, SN-46, SN-47: the article as blocks ------------------------------------------------


@pytest.mark.asyncio
async def test_article_blocks_has_title_and_number(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, text="<p>How to <b>reset</b></p>")]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [(g["sub_type"], g["data"]) for g in groups] == [("content", "# Article 1\n\nKB0000001\n\nHow to **reset**")]


@pytest.mark.asyncio
async def test_article_record_is_blocks(servicenow_connector) -> None:  # noqa: F811
    record = servicenow_connector._transform_to_article_webpage_record(TableAPIRecord(**_article(1)))

    assert record.mime_type == MimeTypes.BLOCKS.value


@pytest.mark.asyncio
async def test_stream_article_is_blocks(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)]}))
    record = MagicMock(record_type=RecordType.WEBPAGE, external_record_id=KB1, weburl="https://x/kb", is_placeholder=False)

    response = await servicenow_connector.stream_record(record)

    assert response.media_type == MimeTypes.BLOCKS.value
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert block_groups(body)[0]["weburl"] == "https://x/kb"


@pytest.mark.asyncio
async def test_empty_article_streams_no_text(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, text="")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"No content" not in content
    assert block_groups(content)[0]["data"] == "# Article 1\n\nKB0000001"  # only the header


@pytest.mark.asyncio
async def test_wiki_article_streams_wiki_body(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        _article(1, article_type="wiki", text="", wiki="== Reset ==\n'''Hold''' the button")]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert groups[0]["data"] == "# Article 1\n\nKB0000001\n\n== Reset ==\n'''Hold''' the button"
    record = servicenow_connector._transform_to_article_webpage_record(TableAPIRecord(**_article(
        1, article_type="wiki", text="", wiki="== Reset ==")))
    assert record.external_revision_id != servicenow_connector._transform_to_article_webpage_record(
        TableAPIRecord(**_article(1, article_type="wiki", text="", wiki="== Other =="))).external_revision_id


@pytest.mark.asyncio
async def test_two_threaded_comments_stream_two_comment_groups(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [_article(1)],
        "kb_feedback": [_feedback(2, parent_comment="fb01", root_feedback="fb01", comments="a reply"),
                        _feedback(1, comments="a question")],
    }))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    comments = [(g["index"], g["parent_index"], g["name"], g["data"]) for g in groups if g["sub_type"] == "comment"]
    assert comments == [(1, 0, "Comment by Reader 1", "a question"), (2, 1, "Comment by Reader 2", "a reply")]
    assert groups[1]["children"] is not None  # the reply hangs under the question


@pytest.mark.asyncio
async def test_feedback_on_old_version_included(servicenow_connector) -> None:  # noqa: F811
    # Feedback stays on the version it was given to; the article is the current version.
    _wire(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [_article(1, EARLIER, sys_id=V1, workflow_state="outdated"), _article(1, LATER, sys_id=V2)],
        "kb_feedback": [_feedback(1, article=V1, comments="on v1"), _feedback(2, article=V2, comments="on v2"),
                        _feedback(3, article="other", number="KB0000002", comments="another article")],
    }))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [g["data"] for g in groups if g["sub_type"] == "comment"] == ["on v1", "on v2"]


@pytest.mark.asyncio
async def test_feedback_summary_counts_helpful_and_ratings(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)], "kb_feedback": [
        _feedback(1, useful="yes", rating="5", comments=""), _feedback(2, useful="yes", rating="3", comments=""),
        _feedback(3, useful="no", rating="0", comments=""),
    ]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [g["data"] for g in groups[1:]] == ["2 of 3 readers found this article helpful. Rated 4.0 of 5 by 2 readers."]


@pytest.mark.asyncio
async def test_flagged_feedback_not_indexed(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)], "kb_feedback": [
        _feedback(1, flagged="true", comments="rude words", useful="no", rating="1"), _feedback(2, comments="fine")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"rude words" not in content
    assert [g["data"] for g in block_groups(content) if g["sub_type"] == "comment"] == ["fine"]
    assert b"helpful" not in content and b"Rated" not in content  # its vote and rating are left out too


@pytest.mark.asyncio
async def test_work_notes_not_indexed(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)], "kb_feedback": [
        _feedback(1, reason="inaccurate: names a customer", work_notes="internal note")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"names a customer" not in content and b"internal note" not in content
    assert not {"reason", "work_notes"} & set(_FEEDBACK_FIELDS)  # never even read


@pytest.mark.asyncio
async def test_new_feedback_changes_revision(servicenow_connector) -> None:  # noqa: F811
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [_article(1)], "kb_feedback": [_feedback(1, article=V1)]}))
    edited = await _revision(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [_article(1)], "kb_feedback": [_feedback(1, article=V1, sys_updated_on=LATER)]}))

    assert len({before, after, edited}) == 3


@pytest.mark.asyncio
async def test_feedback_without_article_bump_reprocesses_article(servicenow_connector) -> None:  # noqa: F811
    # The article row is older than the checkpoint; only its (old version's) feedback is new.
    sc = servicenow_connector
    fake = FakeServiceNow({
        "kb_knowledge": [_article(1, EARLIER, sys_id=V1, workflow_state="outdated"), _article(1, EARLIER, sys_id=V2)],
        "kb_feedback": [_feedback(1, article=V1, sys_updated_on=LATER)],
    })
    store = _wire(sc, fake, sync_point=migrated_sync_point(T0, feedback={"last_sync_time": T0}))

    await sc._sync_articles()

    assert store.keys() == [KB1]
    assert store.records[KB1].external_revision_id == sc._content_revision("<p>body</p>", [["fb01", LATER]])
    assert sc.article_sync_point.data["feedback"] == {"last_sync_time": LATER}
    assert sc.article_sync_point.data["articles"]["last_sync_time"] == T0  # not moved by feedback


@pytest.mark.asyncio
async def test_failed_feedback_listing_skips_the_page(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [_article(1)]},
                          fail=lambda table, q: status_error(500) if table == "kb_feedback" else None)
    store = _wire(sc, fake)

    await sc._sync_articles()

    assert store.keys() == []  # not written with a revision that lacks its feedback
    assert sc.full_sync_coverage().complete is False


# --- SN-48: inline images ---------------------------------------------------------------------


def _with_image(sc, src: str, response: httpx.Response | Exception | None = None) -> AsyncMock:
    fake = FakeServiceNow({"kb_knowledge": [_article(1, text=f'<p>See <img src="{src}" alt="x"></p>')]})
    fake.stream_attachment = AsyncMock(
        side_effect=response if isinstance(response, Exception) else None,
        return_value=response or file_response(PNG, "image/png"),
    )
    _wire(sc, fake)
    return fake.stream_attachment


@pytest.mark.asyncio
@pytest.mark.parametrize("src", [
    f"/sys_attachment.do?sys_id={IMG}",
    f"{INSTANCE}/sys_attachment.do?sys_id={IMG}&amp;view=true",
])
async def test_inline_sys_attachment_image_becomes_data_uri(servicenow_connector, src) -> None:  # noqa: F811
    fetch = _with_image(servicenow_connector, src)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert f"data:image/png;base64,{base64.b64encode(PNG).decode()}" in data
    fetch.assert_awaited_once_with(IMG)


@pytest.mark.asyncio
async def test_html_escaped_sys_id_src_matched(servicenow_connector) -> None:  # noqa: F811
    # As the instance stores it: the "=" escaped
    fetch = _with_image(servicenow_connector, f"/sys_attachment.do?sys_id&#61;{IMG}")

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:image/png;base64," in data
    fetch.assert_awaited_once_with(IMG)


@pytest.mark.asyncio
async def test_oversize_image_left_as_link(servicenow_connector, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(ServiceNowDefaults, "MAX_INLINE_IMAGE_BYTES", len(PNG) - 1)
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}")

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:" not in data and f"sys_attachment.do?sys_id={IMG}" in data


@pytest.mark.asyncio
async def test_external_image_not_fetched(servicenow_connector) -> None:  # noqa: F811
    fetch = _with_image(servicenow_connector, "https://cdn.example.com/sys_attachment.do?sys_id=" + IMG)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "https://cdn.example.com/" in data
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_image_left_as_link(servicenow_connector) -> None:  # noqa: F811
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", status_error(404))

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert f"sys_attachment.do?sys_id={IMG}" in data


@pytest.mark.asyncio
async def test_rate_limited_image_fails_the_stream(servicenow_connector) -> None:  # noqa: F811
    # Indexing tries the article again later rather than index it without the image.
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", ServiceNowRateLimited(0.0))

    with pytest.raises(HTTPException):
        await servicenow_connector._fetch_article_content(KB1)


@pytest.mark.asyncio
async def test_inline_images_get_no_file_record(servicenow_connector) -> None:  # noqa: F811
    # One home: the image is in the article's body, not a file of its own, so the attachment prune
    # (which compares with the kb_knowledge attachment listing) has nothing of it to remove.
    fake = FakeServiceNow({"kb_knowledge": [_article(1)], "sys_attachment": [
        {"sys_id": IMG, "file_name": "image.png", "content_type": "image/png", "size_bytes": "9",
         "table_name": "ZZ_YYkb_knowledge", "table_sys_id": "a0001", "sys_created_on": T0, "sys_updated_on": T0}]})
    store = _wire(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


# --- SN-49: attachments stream in chunks -------------------------------------------------------


def _datasource(handler) -> ServiceNowDataSource:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    inner = SimpleNamespace(headers={"Authorization": "Bearer t"}, _ensure_client=AsyncMock(return_value=http))
    return ServiceNowDataSource(SimpleNamespace(get_client=lambda: inner, get_base_url=lambda: f"{INSTANCE}/api/now"))


@pytest.mark.asyncio
async def test_attachment_streams_in_chunks(servicenow_connector) -> None:  # noqa: F811
    body = b"x" * (3 * 64 * 1024 + 5)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})

    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(handler))
    chunks = [c async for c in await servicenow_connector._open_attachment("att1")]

    assert len(chunks) == 4 and b"".join(chunks) == body
    assert seen == [f"{INSTANCE}/api/now/attachment/att1/file"]


@pytest.mark.asyncio
async def test_404_raised_before_response(servicenow_connector) -> None:  # noqa: F811
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(404, json={"error": {"message": "No Record found"}})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="application/pdf",
                       is_placeholder=False)

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(record)

    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_zero_byte_attachment_streams_200(servicenow_connector) -> None:  # noqa: F811
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(200, content=b"", headers={"content-type": "text/plain"})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="text/plain",
                       record_name="empty.txt", is_placeholder=False)

    response = await servicenow_connector.stream_record(record)

    assert response.status_code == 200
    assert [c async for c in response.body_iterator] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("mime", "rejected"), [("application/pdf", True), (MimeTypes.HTML.value, False)])
async def test_html_body_for_pdf_rejected(servicenow_connector, mime, rejected) -> None:  # noqa: F811
    # A hibernating instance answers every URL with its own HTML page.
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(200, content=b"<html>Your instance is hibernating</html>",
                                 headers={"content-type": "text/html;charset=UTF-8"})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type=mime,
                       record_name="f", is_placeholder=False)

    if rejected:
        with pytest.raises(HTTPException) as raised:
            await servicenow_connector.stream_record(record)
        assert raised.value.status_code == 502
    else:
        response = await servicenow_connector.stream_record(record)
        assert b"".join([c async for c in response.body_iterator]).startswith(b"<html>")
