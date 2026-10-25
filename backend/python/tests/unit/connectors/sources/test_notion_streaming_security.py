"""Notion connector streaming and security: guarded fetches of user-supplied URLs, no stale signed URLs,
no partial content indexed as complete, placeholder stubs, and bounded API fan-out."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.notion.connector import NotionConnector
from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType, WebpageRecord
from app.utils.public_http import PublicFetchResponse, UnsafeUrlError


def _connector() -> NotionConnector:
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_records = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_user_by_source_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])
    conn = NotionConnector(
        logger=MagicMock(),
        data_entities_processor=dep,
        data_store_provider=MagicMock(),
        config_service=AsyncMock(),
        connector_id="notion-1",
        scope="team",
        created_by="user-1",
    )
    conn.data_source = MagicMock()
    return conn


def _resp(success: bool = True, data: object = None, status: int = 200, error: object = None) -> MagicMock:
    r = MagicMock()
    r.success, r.status_code, r.error = success, status, error
    if data is None:
        r.data = None
    else:
        r.data = MagicMock()
        r.data.json.return_value = data
    return r


def _file_record(external_id: str = "block-1", name: str = "report.pdf") -> FileRecord:
    return FileRecord(
        org_id="org-1", record_name=name, record_type=RecordType.FILE, external_record_id=external_id,
        connector_id="notion-1", connector_name=Connectors.NOTION, record_group_type=RecordGroupType.NOTION_WORKSPACE,
        external_record_group_id="ws", mime_type=MimeTypes.PDF.value, version=0, origin=OriginTypes.CONNECTOR,
        is_file=True, size_in_bytes=0,
    )


def _with_datasource(conn: NotionConnector, **methods: object) -> MagicMock:
    ds = MagicMock()
    for name, value in methods.items():
        setattr(ds, name, value)
    conn._get_fresh_datasource = AsyncMock(return_value=ds)
    return ds


# ---- files ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_external_file_on_a_private_address_is_refused_without_a_request() -> None:
    conn = _connector()
    _with_datasource(conn, retrieve_block=AsyncMock(return_value=_resp(data={
        "type": "file", "file": {"type": "external", "external": {"url": "http://169.254.169.254/latest/meta-data"}}})))
    with patch("httpx.AsyncClient.send", new=AsyncMock(side_effect=AssertionError("no request may leave"))):
        with pytest.raises(HTTPException) as exc:
            await conn.stream_record(_file_record())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_notion_hosted_file_streams_with_a_unicode_name() -> None:
    conn = _connector()
    _with_datasource(conn, retrieve_block=AsyncMock(return_value=_resp(data={
        "type": "pdf", "pdf": {"type": "file", "file": {"url": "https://prod-files-secure.s3.amazonaws.com/a/b.pdf?X-Amz-Signature=s"}}})))
    seen = {}

    async def fake_stream(url: str, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
        seen["url"] = url
        yield b"%PDF"

    with patch("app.connectors.sources.notion.connector.stream_content", new=fake_stream):
        response = await conn.stream_record(_file_record(name='报告 "q".pdf'))
    chunks = [c async for c in response.body_iterator]
    assert b"".join(chunks) == b"%PDF"
    assert seen["url"].startswith("https://prod-files-secure")
    assert "UTF-8''" in response.headers["content-disposition"]
    response.headers["content-disposition"].encode("latin-1")  # starlette needs latin-1 headers


@pytest.mark.asyncio
async def test_signed_url_is_never_the_stale_stored_one() -> None:
    conn = _connector()
    _with_datasource(conn, retrieve_block=AsyncMock(return_value=_resp(data=None)),
                     retrieve_comment=AsyncMock(return_value=_resp(data={"attachments": []})))
    record = _file_record()
    record.signed_url = "https://stale.example/expired"
    assert await conn.get_signed_url(record) is None
    record = _file_record(external_id="ca_c1_report.pdf")
    record.signed_url = "https://stale.example/expired"
    assert await conn.get_signed_url(record) is None


# ---- images --------------------------------------------------------------------------------------

def _image(url: str) -> Block:
    return Block(index=0, type=BlockType.IMAGE, format=DataFormat.TXT, data="caption",
                 public_data_link=url, source_id="img-1")


@pytest.mark.asyncio
async def test_image_on_a_private_address_is_demoted_not_fetched() -> None:
    conn = _connector()
    block = _image("http://127.0.0.1:8080/secret.png")
    await conn._convert_image_blocks_to_base64([block])
    assert block.type == BlockType.TEXT and block.public_data_link is None


@pytest.mark.asyncio
async def test_images_are_fetched_a_few_at_a_time_and_logs_hide_signatures() -> None:
    conn = _connector()
    running, peak = 0, 0

    async def slow_fetch(url: str) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        raise UnsafeUrlError("blocked")

    conn._fetch_image = slow_fetch
    blocks = [_image(f"https://files.example/{i}.png?X-Amz-Signature=secret") for i in range(20)]
    await conn._convert_image_blocks_to_base64(blocks)
    assert peak <= 4
    logged = " ".join(str(c) for c in conn.logger.warning.call_args_list)
    assert "secret" not in logged


@pytest.mark.asyncio
async def test_image_bytes_become_base64() -> None:
    conn = _connector()
    conn._fetch_image = AsyncMock(return_value=PublicFetchResponse(
        url="u", status_code=200, headers={"content-type": "image/png"}, content=b"\x89PNG"))
    block = _image("https://files.example/a.png")
    await conn._convert_image_blocks_to_base64([block])
    assert block.format == DataFormat.BASE64 and block.data["uri"].startswith("data:image/png;base64,")


# ---- partial content -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_row_page_fails_the_data_source_stream() -> None:
    conn = _connector()
    _with_datasource(
        conn,
        retrieve_data_source_by_id=AsyncMock(return_value=_resp(data={"properties": {}})),
        query_data_source_by_id=AsyncMock(side_effect=[
            _resp(data={"results": [{"id": "r1"}], "has_more": True, "next_cursor": "c"}),
            _resp(success=False, status=500, error="HTTP 500"),
        ]),
    )
    with pytest.raises(HTTPException):
        await conn._fetch_data_source_as_blocks("ds-1", MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("status,raises", [(429, True), (500, True), (404, False), (403, False)])
async def test_comment_errors_are_errors_but_missing_or_forbidden_means_none(status, raises) -> None:
    conn = _connector()
    conn._is_definitive_not_found = MagicMock(return_value=status == 404)
    _with_datasource(conn, retrieve_comments=AsyncMock(return_value=_resp(success=False, status=status,
                                                                          error=f"HTTP {status}")))
    if raises:
        with pytest.raises(HTTPException):
            await conn._fetch_comments_for_blocks("page-1", ["b1"])
    else:
        assert await conn._fetch_comments_for_blocks("page-1", ["b1"]) == []


@pytest.mark.asyncio
async def test_synced_block_with_an_unreadable_original_renders_empty() -> None:
    conn = _connector()
    conn._process_blocks_recursive = AsyncMock(side_effect=HTTPException(404, "gone"))
    assert await conn._process_children_of("ref-1", "original-1", MagicMock(), [], [], None) == []
    with pytest.raises(HTTPException):
        await conn._process_children_of("block-1", "block-1", MagicMock(), [], [], None)


# ---- stubs and links -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_stubs_are_placeholders() -> None:
    conn = _connector()
    conn.resolve_page_title_by_id = AsyncMock(return_value="Target")
    await conn._batch_get_or_create_child_records({"p1": ("Child", RecordType.WEBPAGE, "parent-1")})
    child = await conn.get_record_child_by_external_id("rel-1", None)
    created = [rec for call in conn.data_entities_processor.on_new_records.call_args_list for rec, _ in call.args[0]]
    assert all(r.is_placeholder and r.version == 0 for r in created)
    relation = next(r for r in created if r.external_record_id == "rel-1")
    assert relation.parent_external_record_id is None and child.child_id == relation.id


@pytest.mark.asyncio
async def test_comment_attachment_links_to_the_stored_record() -> None:
    conn = _connector()
    stored = MagicMock(id="stored-1", record_name="report.pdf")
    conn.get_record_by_external_id = AsyncMock(return_value=stored)
    conn._transform_to_comment_file_record = AsyncMock(return_value=_file_record("ca_c1_report.pdf"))
    _, attachments = await conn._process_comment_attachments(
        {"attachments": [{"file": {"url": "https://f/x/report.pdf"}}]}, "c1", "page-1", None)
    assert [a.id for a in attachments] == ["stored-1"]
    conn.get_record_by_external_id = AsyncMock(return_value=None)
    _, attachments = await conn._process_comment_attachments(
        {"attachments": [{"file": {"url": "https://f/x/report.pdf"}}]}, "c1", "page-1", None)
    assert attachments == []


# ---- API cost ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_row_sub_pages_come_from_the_graph_not_one_call_per_row() -> None:
    conn = _connector()
    ds = _with_datasource(conn, retrieve_block_children=AsyncMock(side_effect=AssertionError("no API call")))
    child = WebpageRecord(org_id="org-1", record_name="Sub", record_type=RecordType.WEBPAGE,
                          external_record_id="sub-1", connector_id="notion-1", connector_name=Connectors.NOTION,
                          record_group_type=RecordGroupType.NOTION_WORKSPACE, external_record_group_id="ws",
                          mime_type=MimeTypes.BLOCKS.value, version=0, origin=OriginTypes.CONNECTOR)
    conn.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[child])
    rows = [Block(index=i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={}, source_id=f"row-{i}")
            for i in range(30)]
    await conn._resolve_table_row_children(rows)
    assert ds.retrieve_block_children.await_count == 0
    assert rows[0].table_row_metadata.children_records[0].child_id == child.id


@pytest.mark.asyncio
async def test_quoted_text_comes_from_the_rendered_block() -> None:
    conn = _connector()
    conn._extract_block_text_content = AsyncMock(side_effect=AssertionError("no block GET"))
    conn._create_block_comment_from_notion_comment = AsyncMock(return_value=(None, []))
    block = Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="the quoted text", source_id="b1")
    await conn._attach_comments_to_blocks([block], [], {"b1": [({"id": "c"}, "b1")]}, "page-1", None, MagicMock())
    assert conn._create_block_comment_from_notion_comment.await_args.kwargs["quoted_text"] == "the quoted text"


@pytest.mark.asyncio
async def test_stream_comments_cover_rendered_blocks_only() -> None:
    conn = _connector()
    conn._fetch_comments_for_blocks = AsyncMock(return_value=[])
    blocks = [
        Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="x", source_id="b1"),
        Block(index=1, type=BlockType.TEXT, sub_type=BlockSubType.CHILD_RECORD, format=DataFormat.TXT,
              data="child", source_id="child-page"),
    ]
    await conn._attach_stream_comments(BlocksContainer(blocks=blocks, block_groups=[]), "page-1", None, MagicMock())
    assert conn._fetch_comments_for_blocks.await_args.args == ("page-1", ["b1"])
