"""ServiceNow article content: a revision that changes only with what an article streams, the article as
blocks (title and number, body, wiki body, feedback comments and a feedback summary), inline images
embedded as data: URIs, and attachments streamed in chunks.

The Table API is ``FakeServiceNow``; the stored records are ``FakeRecordStore``.
"""

import base64
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from servicenow_fakes import (
    EARLIER,
    INSTANCE,
    KB1,
    LATER,
    T0,
    V1,
    V2,
    FakeServiceNow,
    MemorySyncPoint,
    article_row,
    attachment_row,
    block_groups,
    category_row,
    file_key,
    file_response,
    kb_row,
    migrated_sync_point,
    status_error,
    stored_record,
    wire_store,
)

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.servicenow.servicenow.connector import _FEEDBACK_FIELDS
from app.connectors.sources.servicenow.servicenow.constants import ServiceNowDefaults
from app.models.entities import FileRecord, RecordType
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import ServiceNowAPIError, TableAPIRecord
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
    store = wire_store(sc, fake)
    await sc._sync_articles()
    return store.records[KB1].external_revision_id


# --- the revision is what the article streams ----------------------------------------------------


@pytest.mark.asyncio
async def test_title_only_change_keeps_revision(servicenow_connector) -> None:
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        article_row(1, LATER, short_description="Renamed")]}))

    assert before == after


@pytest.mark.asyncio
async def test_move_keeps_revision(servicenow_connector) -> None:
    # The processor refreshes the vectors' groups on a move (syncVectorMembership), so no re-index.
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        article_row(1, LATER, kb_category="cat2")]}))

    assert before == after


@pytest.mark.asyncio
async def test_body_change_changes_revision(servicenow_connector) -> None:
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        article_row(1, LATER, text="<p>edited</p>")]}))

    assert before != after


@pytest.mark.asyncio
async def test_new_version_same_body_keeps_revision(servicenow_connector) -> None:
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, EARLIER, sys_id=V1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        article_row(1, EARLIER, sys_id=V1, workflow_state="outdated"), article_row(1, LATER, sys_id=V2)]}))

    assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize(("copy_hash", "same"), [("h-img", True), ("h-other", False)])
async def test_new_version_with_copied_images_keeps_revision(servicenow_connector, copy_hash, same) -> None:
    # A checkout copies the inline images with new sys_ids and rewrites the body to them.
    def version(sys_id: str, image: str, image_hash: str, updated: str) -> FakeServiceNow:
        return FakeServiceNow({"kb_knowledge": [article_row(1, updated, sys_id=sys_id, text=f'<img src="/sys_attachment.do?sys_id&#61;{image}">')],
                               "sys_attachment": [{**_image_row(image, sys_id), "hash": image_hash}]})

    before = await _revision(servicenow_connector, version(V1, "1" * 32, "h-img", EARLIER))
    after = await _revision(servicenow_connector, version(V2, "2" * 32, copy_hash, LATER))

    assert (before == after) is same


def test_attachment_revision_is_size_and_creation(servicenow_connector) -> None:
    from app.sources.external.servicenow.models import AttachmentMetadata

    def build(updated: str) -> FileRecord | None:
        return servicenow_connector._transform_to_attachment_file_record(AttachmentMetadata(
            sys_id="f1", file_name="a.pdf", content_type="application/pdf", size_bytes="10",
            table_sys_id="a0001", sys_created_on="2015-01-01 00:00:00", sys_updated_on=updated))

    assert build("2015-01-01 00:00:00").external_revision_id == build("2026-01-01 00:00:00").external_revision_id


def test_attachment_revision_is_its_hash_which_a_copy_keeps(servicenow_connector) -> None:
    from app.sources.external.servicenow.models import AttachmentMetadata

    def build(sys_id: str, created: str) -> FileRecord | None:
        return servicenow_connector._transform_to_attachment_file_record(AttachmentMetadata(
            sys_id=sys_id, file_name="a.pdf", content_type="application/pdf", size_bytes="10", hash="h1",
            table_sys_id="a0001", sys_created_on=created, sys_updated_on=created))

    assert build("f1", "2015-01-01 00:00:00").external_revision_id == "h1"
    assert build("f2", "2026-01-01 00:00:00").external_revision_id == "h1"


# --- the article as blocks ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_article_blocks_has_title_and_number(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, text="<p>How to <b>reset</b></p>")]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [(g["sub_type"], g["data"]) for g in groups] == [("content", "# Article 1\n\nKB0000001\n\nHow to **reset**")]


@pytest.mark.asyncio
async def test_article_record_is_blocks(servicenow_connector) -> None:
    record = servicenow_connector._transform_to_article_webpage_record(TableAPIRecord(**article_row(1)))

    assert record.mime_type == MimeTypes.BLOCKS.value


@pytest.mark.asyncio
async def test_stream_article_is_blocks(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    record = MagicMock(record_type=RecordType.WEBPAGE, external_record_id=KB1, weburl="https://x/kb", is_placeholder=False)

    response = await servicenow_connector.stream_record(record)

    assert response.media_type == MimeTypes.BLOCKS.value
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert block_groups(body)[0]["weburl"] == "https://x/kb"


@pytest.mark.asyncio
async def test_empty_article_streams_no_text(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, text="")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"No content" not in content
    assert block_groups(content)[0]["data"] == "# Article 1\n\nKB0000001"  # only the header


@pytest.mark.asyncio
async def test_wiki_article_streams_wiki_body(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [
        article_row(1, article_type="wiki", text="", wiki="== Reset ==\n'''Hold''' the button")]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert groups[0]["data"] == "# Article 1\n\nKB0000001\n\n== Reset ==\n'''Hold''' the button"
    record = servicenow_connector._transform_to_article_webpage_record(TableAPIRecord(**article_row(
        1, article_type="wiki", text="", wiki="== Reset ==")))
    assert record.external_revision_id != servicenow_connector._transform_to_article_webpage_record(
        TableAPIRecord(**article_row(1, article_type="wiki", text="", wiki="== Other =="))).external_revision_id


@pytest.mark.asyncio
async def test_two_threaded_comments_stream_two_comment_groups(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [article_row(1)],
        "kb_feedback": [_feedback(2, parent_comment="fb01", root_feedback="fb01", comments="a reply"),
                        _feedback(1, comments="a question")],
    }))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    comments = [(g["index"], g["parent_index"], g["name"], g["data"]) for g in groups if g["sub_type"] == "comment"]
    assert comments == [(1, 0, "Comment by Reader 1", "a question"), (2, 1, "Comment by Reader 2", "a reply")]
    assert groups[1]["children"] is not None  # the reply hangs under the question


@pytest.mark.asyncio
async def test_feedback_on_old_version_included(servicenow_connector) -> None:
    # Feedback stays on the version it was given to; the article is the current version.
    wire_store(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [article_row(1, EARLIER, sys_id=V1, workflow_state="outdated"), article_row(1, LATER, sys_id=V2)],
        "kb_feedback": [_feedback(1, article=V1, comments="on v1"), _feedback(2, article=V2, comments="on v2"),
                        _feedback(3, article="other", number="KB0000002", comments="another article")],
    }))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [g["data"] for g in groups if g["sub_type"] == "comment"] == ["on v1", "on v2"]


@pytest.mark.asyncio
async def test_feedback_summary_counts_helpful_and_ratings(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)], "kb_feedback": [
        _feedback(1, useful="yes", rating="5", comments=""), _feedback(2, useful="yes", rating="3", comments=""),
        _feedback(3, useful="no", rating="0", comments=""),
    ]}))

    groups = block_groups(await servicenow_connector._fetch_article_content(KB1))

    assert [g["data"] for g in groups[1:]] == ["2 of 3 readers found this article helpful. Rated 4.0 of 5 by 2 readers."]


@pytest.mark.asyncio
async def test_flagged_feedback_not_indexed(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)], "kb_feedback": [
        _feedback(1, flagged="true", comments="rude words", useful="no", rating="1"), _feedback(2, comments="fine")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"rude words" not in content
    assert [g["data"] for g in block_groups(content) if g["sub_type"] == "comment"] == ["fine"]
    assert b"helpful" not in content and b"Rated" not in content  # its vote and rating are left out too


@pytest.mark.asyncio
async def test_work_notes_not_indexed(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)], "kb_feedback": [
        _feedback(1, reason="inaccurate: names a customer", work_notes="internal note")]}))

    content = await servicenow_connector._fetch_article_content(KB1)

    assert b"names a customer" not in content and b"internal note" not in content
    assert not {"reason", "work_notes"} & set(_FEEDBACK_FIELDS)  # never even read


@pytest.mark.asyncio
async def test_new_feedback_changes_revision(servicenow_connector) -> None:
    before = await _revision(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    after = await _revision(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [article_row(1)], "kb_feedback": [_feedback(1, article=V1)]}))
    edited = await _revision(servicenow_connector, FakeServiceNow({
        "kb_knowledge": [article_row(1)], "kb_feedback": [_feedback(1, article=V1, sys_updated_on=LATER)]}))

    assert len({before, after, edited}) == 3


@pytest.mark.asyncio
async def test_feedback_without_article_bump_reprocesses_article(servicenow_connector) -> None:
    # The article row is older than the checkpoint; only its (old version's) feedback is new.
    sc = servicenow_connector
    fake = FakeServiceNow({
        "kb_knowledge": [article_row(1, EARLIER, sys_id=V1, workflow_state="outdated"), article_row(1, EARLIER, sys_id=V2)],
        "kb_feedback": [_feedback(1, article=V1, sys_updated_on=LATER)],
    })
    store = wire_store(sc, fake, sync_point=migrated_sync_point(T0, feedback={"last_sync_time": T0}))

    await sc._sync_articles()

    assert store.keys() == [KB1]
    assert store.records[KB1].external_revision_id == sc._content_revision("<p>body</p>", [["fb01", LATER]])
    assert sc.article_sync_point.data["feedback"] == {"last_sync_time": LATER, "count": 1,
                                                      "synced_in_overlap": [f"{LATER}|fb01"]}
    assert sc.article_sync_point.data["articles"]["last_sync_time"] == T0  # not moved by feedback


@pytest.mark.asyncio
async def test_deleted_feedback_reindexes_its_article(servicenow_connector) -> None:
    # A deleted row leaves nothing a delta could see; the article row did not change.
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, EARLIER)], "kb_feedback": [_feedback(1)]})
    sync_point = migrated_sync_point(T0, feedback={"last_sync_time": LATER, "count": 2})
    store = wire_store(sc, fake, stored_record(KB1), sync_point=sync_point)

    await sc._sync_articles()

    assert store.records[KB1].external_revision_id == sc._content_revision("<p>body</p>", [["fb01", T0]])
    assert sync_point.data["feedback"]["count"] == 1
    assert sync_point.data["articles"]["last_sync_time"] == T0  # not moved by the re-read


@pytest.mark.asyncio
async def test_failed_feedback_listing_skips_the_page(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)]},
                          fail=lambda table, q: status_error(500) if table == "kb_feedback" else None)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == []  # not written with a revision that lacks its feedback
    assert sc.full_sync_coverage().complete is False


# --- inline images ----------------------------------------------------------------------------


def _image_row(sys_id: str, owner: str = "a0001", table: str = "ZZ_YYkb_knowledge", size: int = len(PNG)) -> dict:
    return {"sys_id": sys_id, "file_name": "image.png", "content_type": "image/png", "size_bytes": str(size),
            "table_name": table, "table_sys_id": owner, "sys_created_on": T0, "sys_updated_on": T0}


def _with_image(sc, src: str, response: httpx.Response | Exception | None = None, **row: str) -> AsyncMock:
    """Article a0001 shows ``src``; its attachment row is ``_image_row(IMG, **row)``."""
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, text=f'<p>See <img src="{src}" alt="x"></p>')],
                           "sys_attachment": [_image_row(IMG, **row)]})
    fake.stream_attachment = AsyncMock(
        side_effect=response if isinstance(response, Exception) else None,
        return_value=response or file_response(PNG, "image/png"),
    )
    wire_store(sc, fake)
    return fake.stream_attachment


@pytest.mark.asyncio
@pytest.mark.parametrize("src", [
    f"/sys_attachment.do?sys_id={IMG}",
    f"{INSTANCE}/sys_attachment.do?sys_id={IMG}&amp;view=true",
])
async def test_inline_sys_attachment_image_becomes_data_uri(servicenow_connector, src) -> None:
    fetch = _with_image(servicenow_connector, src)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert f"data:image/png;base64,{base64.b64encode(PNG).decode()}" in data
    fetch.assert_awaited_once_with(IMG)


@pytest.mark.asyncio
async def test_html_escaped_sys_id_src_matched(servicenow_connector) -> None:
    # As the instance stores it: the "=" escaped
    fetch = _with_image(servicenow_connector, f"/sys_attachment.do?sys_id&#61;{IMG}")

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:image/png;base64," in data
    fetch.assert_awaited_once_with(IMG)


@pytest.mark.asyncio
async def test_oversize_image_left_as_link(servicenow_connector, monkeypatch) -> None:
    monkeypatch.setattr(ServiceNowDefaults, "MAX_INLINE_IMAGE_BYTES", len(PNG) - 1)
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}")

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:" not in data and f"sys_attachment.do?sys_id={IMG}" in data


@pytest.mark.asyncio
async def test_image_listed_over_the_size_cap_is_not_downloaded(servicenow_connector) -> None:
    fetch = _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}",
                        size=ServiceNowDefaults.MAX_INLINE_IMAGE_BYTES + 1)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:" not in data
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"table": "incident", "owner": "inc0001"},  # an incident's screenshot
    {"owner": "a0002"},  # another article's inline image
    {"table": "kb_knowledge", "owner": "a0002"},  # another article's attachment
])
async def test_image_that_is_not_this_articles_stays_a_link(servicenow_connector, row) -> None:
    # Its readers are not the article's: embedded, it would reach every reader of the article.
    fetch = _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", **row)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:" not in data and f"sys_attachment.do?sys_id={IMG}" in data
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_articles_own_attachment_shown_inline_is_embedded(servicenow_connector) -> None:
    fetch = _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", table="kb_knowledge")

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert "data:image/png;base64," in data
    fetch.assert_awaited_once_with(IMG)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [
    ("MAX_INLINE_IMAGES", 2),
    ("MAX_INLINE_IMAGES_TOTAL_BYTES", 2 * len(PNG)),
])
async def test_images_past_the_articles_budget_stay_links(servicenow_connector, monkeypatch, budget) -> None:
    monkeypatch.setattr(ServiceNowDefaults, *budget)
    images = [f"{n}" * 32 for n in (1, 2, 3)]
    fake = FakeServiceNow({
        "kb_knowledge": [article_row(1, text="".join(f'<img src="/sys_attachment.do?sys_id={i}">' for i in images))],
        "sys_attachment": [_image_row(i) for i in images],
    })
    fake.stream_attachment = AsyncMock(side_effect=lambda _: file_response(PNG, "image/png"))
    wire_store(servicenow_connector, fake)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert data.count("data:image/png;base64,") == 2 and f"sys_id={images[2]}" in data
    assert [c.args[0] for c in fake.stream_attachment.await_args_list] == images[:2]


@pytest.mark.asyncio
async def test_external_image_not_fetched(servicenow_connector) -> None:
    url = "https://cdn.example.com/sys_attachment.do?sys_id=" + IMG
    fetch = _with_image(servicenow_connector, url)

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert data.count(url) == 1  # left as the link it was
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_image_left_as_link(servicenow_connector) -> None:
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", status_error(404))

    data = block_groups(await servicenow_connector._fetch_article_content(KB1))[0]["data"]

    assert f"sys_attachment.do?sys_id={IMG}" in data


@pytest.mark.asyncio
async def test_rate_limited_image_fails_the_stream(servicenow_connector) -> None:
    # Indexing tries the article again later rather than index it without the image.
    _with_image(servicenow_connector, f"/sys_attachment.do?sys_id={IMG}", ServiceNowRateLimited(time.time() + 600))

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector._fetch_article_content(KB1)

    assert raised.value.status_code == 429
    assert 590 <= int(raised.value.headers["Retry-After"]) <= 600


@pytest.mark.asyncio
async def test_rate_limited_attachment_stream_is_429(servicenow_connector) -> None:
    servicenow_connector._current_attachment = AsyncMock(side_effect=ServiceNowRateLimited(time.time() + 60))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="application/pdf",
                       is_placeholder=False)

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(record)

    assert raised.value.status_code == 429 and raised.value.headers["Retry-After"].isdigit()


@pytest.mark.asyncio
async def test_429_the_retries_did_not_get_past_stops_the_run(servicenow_connector) -> None:
    # Not one failed article after another: every later call would get the same answer.
    fake = FakeServiceNow({"kb_knowledge": [article_row(i) for i in (1, 2, 3, 4)]},
                          fail=lambda table, q: status_error(429) if table == "sys_attachment" else None)
    wire_store(servicenow_connector, fake)

    with pytest.raises(ServiceNowAPIError) as raised:
        await servicenow_connector._sync_articles()

    assert raised.value.status_code == 429
    assert len(fake.calls_to("sys_attachment")) == 1


@pytest.mark.asyncio
async def test_inline_images_get_no_file_record(servicenow_connector) -> None:
    # One home: the image is in the article's body, not a file of its own, so the attachment prune
    # (which compares with the kb_knowledge attachment listing) has nothing of it to remove.
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)], "sys_attachment": [
        {"sys_id": IMG, "file_name": "image.png", "content_type": "image/png", "size_bytes": "9",
         "table_name": "ZZ_YYkb_knowledge", "table_sys_id": "a0001", "sys_created_on": T0, "sys_updated_on": T0}]})
    store = wire_store(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


# --- attachments stream in chunks --------------------------------------------------------------


def _datasource(handler) -> ServiceNowDataSource:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    inner = SimpleNamespace(headers={"Authorization": "Bearer t"}, _ensure_client=AsyncMock(return_value=http))
    return ServiceNowDataSource(SimpleNamespace(get_client=lambda: inner, get_base_url=lambda: f"{INSTANCE}/api/now"))


@pytest.mark.asyncio
async def test_attachment_streams_in_chunks(servicenow_connector) -> None:
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
async def test_404_raised_before_response(servicenow_connector) -> None:
    servicenow_connector._current_attachment = AsyncMock(return_value="att1")
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(404, json={"error": {"message": "No Record found"}})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="application/pdf",
                       is_placeholder=False)

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(record)

    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_zero_byte_attachment_streams_200(servicenow_connector) -> None:
    servicenow_connector._current_attachment = AsyncMock(return_value="att1")
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(200, content=b"", headers={"content-type": "text/plain"})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="text/plain",
                       record_name="empty.txt", is_placeholder=False)

    response = await servicenow_connector.stream_record(record)

    assert response.status_code == 200
    assert [c async for c in response.body_iterator] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("mime", "rejected"), [("application/pdf", True), (MimeTypes.HTML.value, False)])
async def test_html_body_for_pdf_rejected(servicenow_connector, mime, rejected) -> None:
    servicenow_connector._current_attachment = AsyncMock(return_value="att1")
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


# --- an attachment keeps its record across article versions, and streams its current copy --------


def _versions(copy: dict | None = None, *, current: str = "published") -> FakeServiceNow:
    """KB0000001: v1 (outdated) and v2, whose checkout copied v1's file with a new sys_id."""
    original = {**attachment_row(1, V1), "hash": "h1"}
    return FakeServiceNow({
        "kb_knowledge": [article_row(1, EARLIER, sys_id=V1, workflow_state="outdated"),
                         article_row(1, LATER, sys_id=V2, workflow_state=current)],
        "sys_attachment": [original, copy if copy is not None else
                           {**original, "sys_id": "f0002", "table_sys_id": V2, "sys_created_on": LATER}],
    })


@pytest.mark.asyncio
async def test_new_version_keeps_the_attachment_record_and_revision(servicenow_connector) -> None:
    sc = servicenow_connector
    store = wire_store(sc, FakeServiceNow({"kb_knowledge": [article_row(1, EARLIER, sys_id=V1)],
                                           "sys_attachment": [{**attachment_row(1, V1), "hash": "h1"}]}),
                       sync_point=migrated_sync_point())
    await sc._sync_articles()
    key, before = f"{KB1}/file1.pdf/h1", store.records[f"{KB1}/file1.pdf/h1"]

    sc._get_fresh_datasource = AsyncMock(return_value=_versions())
    sc.article_sync_point = migrated_sync_point(EARLIER)
    await sc._sync_articles()

    assert store.keys("FILE") == [key] and store.deleted == []
    assert store.records[key].id == before.id  # its citations still open
    assert store.records[key].external_revision_id == before.external_revision_id == "h1"  # not indexed again


@pytest.mark.asyncio
async def test_two_identical_files_on_one_article_are_two_records(servicenow_connector) -> None:
    rows = [{**attachment_row(1, "a0001"), "hash": "h1"}, {**attachment_row(1, "a0001"), "sys_id": "f0009", "hash": "h1"}]
    store = wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1)], "sys_attachment": rows}))

    await servicenow_connector._sync_articles()

    assert store.keys("FILE") == [f"{KB1}/file1.pdf/h1", f"{KB1}/file1.pdf/h1#2"]


def _file(external_id: str) -> SimpleNamespace:
    return SimpleNamespace(**vars(stored_record(external_id, "FILE", parent=KB1)), record_name="file1.pdf",
                           mime_type="application/pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", [f"{KB1}/file1.pdf/h1", "f0002"])  # by key, and stored under its sys_id
async def test_attachment_streams_the_current_versions_copy(servicenow_connector, external_id) -> None:
    wire_store(servicenow_connector, _versions())

    assert await servicenow_connector._current_attachment(_file(external_id)) == "f0002"


@pytest.mark.asyncio
@pytest.mark.parametrize("fake", [
    _versions(current="retired"),  # the article is no longer current
    _versions({**attachment_row(3, V2), "hash": "h3"}),  # the current version no longer has the file
])
async def test_attachment_not_on_a_current_version_is_404(servicenow_connector, fake) -> None:
    fake.stream_attachment = AsyncMock()
    wire_store(servicenow_connector, fake)

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(_file(f"{KB1}/file1.pdf/h1"))

    assert raised.value.status_code == 404
    fake.stream_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_upgrade_moves_attachments_to_their_key_once(servicenow_connector) -> None:
    # An install whose articles are keyed by number and whose files are under their sys_id.
    sc = servicenow_connector
    sync_point = MemorySyncPoint({**migrated_sync_point(T0).data})
    del sync_point.data["attachment_key/v2"]
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, EARLIER)], "sys_attachment": [attachment_row(1, "a0001")]})
    store = wire_store(sc, fake, stored_record(KB1), _file("f0001"), sync_point=sync_point)

    await sc._sync_articles()

    assert "sys_updated_on>" not in fake.calls_to("kb_knowledge")[0]  # read in full, checkpoint or not
    assert store.keys("FILE") == [file_key(1)]
    assert store.records[file_key(1)].id == "id-f0001"  # the same record, so its citations still open
    assert sync_point.data["attachment_key/v2"] == {"done": True}

    fake.calls.clear()
    await sc._sync_articles()

    assert "sys_updated_on>" in fake.calls_to("kb_knowledge")[0]  # once


# --- names in any script, and files too large to hold ---------------------------------------------

UNICODE = "Ünïcødé — 漢字 🚀"


@pytest.mark.asyncio
async def test_unicode_names_survive_the_transforms_and_the_streams(servicenow_connector) -> None:
    sc = servicenow_connector
    store = wire_store(sc, FakeServiceNow({
        "kb_knowledge": [article_row(1, short_description=UNICODE, text=f"<p>{UNICODE}</p>")],
        "sys_attachment": [{**attachment_row(1, "a0001"), "file_name": f"{UNICODE}.pdf"}],
    }))
    kb = sc._transform_to_kb_record_group(TableAPIRecord(**kb_row("kb1", title=UNICODE)))
    category = sc._transform_to_category_record_group(TableAPIRecord(**category_row(
        "cat1", "kb1", label=UNICODE, value="u", parent_table="kb_knowledge_base", active="true", sys_created_on=T0)))

    await sc._sync_articles()

    assert (kb.name, category.name, store.records[KB1].record_name) == (UNICODE, UNICODE, UNICODE)
    key = f"{KB1}/{UNICODE}.pdf/10"
    assert store.records[key].record_name == f"{UNICODE}.pdf"
    article = await sc.stream_record(store.records[KB1])
    assert block_groups(b"".join([c async for c in article.body_iterator]))[0]["data"] == \
        f"# {UNICODE}\n\n{KB1}\n\n{UNICODE}"
    sc._current_attachment = AsyncMock(return_value="f0001")
    sc._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})))
    file = await sc.stream_record(store.records[key])
    assert b"".join([c async for c in file.body_iterator]) == b"%PDF"
    assert file.headers["content-disposition"].startswith("attachment;")


@pytest.mark.asyncio
async def test_a_200_mb_attachment_streams_without_being_held_in_memory(servicenow_connector) -> None:
    chunk, count = b"\0" * 64 * 1024, 3200  # 200 MiB
    produced: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for _ in range(count):
            produced.append(1)
            yield chunk

    servicenow_connector._current_attachment = AsyncMock(return_value="att1")
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=_datasource(
        lambda _: httpx.Response(200, content=body(), headers={"content-type": "application/octet-stream"})))
    record = MagicMock(record_type=RecordType.FILE, external_record_id="att1", mime_type="application/octet-stream",
                       record_name="big.bin", is_placeholder=False)

    response = await servicenow_connector.stream_record(record)
    stream = response.body_iterator
    first = await anext(stream)

    assert len(produced) <= 2  # only what was sent so far was read from ServiceNow
    total = len(first)
    async for part in stream:
        total += len(part)
        assert len(part) <= 64 * 1024
    assert total == count * len(chunk) and len(produced) == count
