"""Unit tests for github_teams StreamingHelper.

Covers:
- stream_record dispatch by record type (TICKET/PULL_REQUEST/FILE/CODE_FILE).
- Unsupported record type raises ValueError.
- reindex_records: routes TICKET/PULL_REQUEST through the reindex-check hooks
  and republishes the rest via reindex_existing_records.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.streaming import StreamingHelper
from app.models.entities import CodeFileRecord, FileRecord, PullRequestRecord, Record, TicketRecord

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _record(record_type: str, **kwargs: object) -> Record:
    base = dict(
        id="rec-1", org_id="org-1", record_name="x", record_type=record_type,
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id="ext-1",
    )
    base.update(kwargs)
    return Record(**base)


class TestStreamRecordDispatch:
    async def test_ticket_streams_blocks_container(self) -> None:
        c = make_mock_connector()
        c.issues.build_ticket_blocks = AsyncMock(return_value='{"blocks": []}')
        helper = StreamingHelper(c)

        response = await helper.stream_record(_record("TICKET"))

        c.issues.build_ticket_blocks.assert_awaited_once()
        assert response.media_type == "application/blocks"

    async def test_pull_request_streams_blocks_container(self) -> None:
        c = make_mock_connector()
        c.pull_requests.build_pull_request_blocks = AsyncMock(return_value='{"blocks": []}')
        helper = StreamingHelper(c)

        await helper.stream_record(_record("PULL_REQUEST"))

        c.pull_requests.build_pull_request_blocks.assert_awaited_once()

    async def test_ticket_content_disposition_sanitizes_non_latin1_title(self) -> None:
        """Issue titles are arbitrary user text; a raw non-latin-1 title in the
        header would raise UnicodeEncodeError when the ASGI server encodes it."""
        c = make_mock_connector()
        c.issues.build_ticket_blocks = AsyncMock(return_value="{}")
        helper = StreamingHelper(c)
        record = _record("TICKET", record_name="Fix caf\u00e9 crash \U0001F41B")

        response = await helper.stream_record(record)

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in response.raw_headers}
        header_value = headers["content-disposition"]
        header_value.encode("latin-1")  # must not raise
        assert "caf" in header_value

    async def test_pull_request_content_disposition_escapes_quotes_and_control_chars(self) -> None:
        c = make_mock_connector()
        c.pull_requests.build_pull_request_blocks = AsyncMock(return_value="{}")
        helper = StreamingHelper(c)
        record = _record("PULL_REQUEST", record_name='Fix "bug"\r\nX-Injected: evil')

        response = await helper.stream_record(record)

        header_value = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in response.raw_headers
        }["content-disposition"]
        header_value.encode("latin-1")  # must not raise
        assert "\r" not in header_value and "\n" not in header_value

    async def test_file_record_streams_attachment_content(self) -> None:
        c = make_mock_connector()
        c.comments.fetch_attachment_content = AsyncMock(return_value=b"hello")
        helper = StreamingHelper(c)
        record = FileRecord(
            id="rec-1", org_id="org-1", record_name="x.pdf", record_type="FILE",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="ext-1", is_file=True,
        )

        await helper.stream_record(record)
        c.comments.fetch_attachment_content.assert_awaited_once_with(record)

    async def test_code_file_record_streams_via_repos(self) -> None:
        c = make_mock_connector()
        c.repos.fetch_code_file_content = AsyncMock(return_value=b"print(1)")
        helper = StreamingHelper(c)
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="a.py", record_type="CODE_FILE",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="/1/blob/a.py", file_path="a.py",
        )

        await helper.stream_record(record)
        c.repos.fetch_code_file_content.assert_awaited_once_with(record)

    async def test_unsupported_record_type_raises(self) -> None:
        c = make_mock_connector()
        helper = StreamingHelper(c)
        with pytest.raises(ValueError):
            await helper.stream_record(_record("COMMENT"))

    async def test_wrong_class_for_code_file_type_raises(self) -> None:
        """A base Record with record_type=CODE_FILE (not a real CodeFileRecord)
        must fail loudly rather than silently streaming nothing."""
        c = make_mock_connector()
        helper = StreamingHelper(c)
        with pytest.raises(ValueError):
            await helper.stream_record(_record("CODE_FILE"))


def _ticket_record(**kwargs: object) -> TicketRecord:
    base = dict(
        id="rec-1", org_id="org-1", record_name="Issue #1", record_type="TICKET",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id="ext-issue-1",
    )
    base.update(kwargs)
    return TicketRecord(**base)


def _pr_record(**kwargs: object) -> PullRequestRecord:
    base = dict(
        id="rec-2", org_id="org-1", record_name="PR #1", record_type="PULL_REQUEST",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id="ext-pr-1",
    )
    base.update(kwargs)
    return PullRequestRecord(**base)


class TestReindexRecords:
    async def test_ticket_and_pr_route_through_reindex_check(self) -> None:
        c = make_mock_connector()
        ticket = _ticket_record()
        pr = _pr_record()
        c.issues.check_and_fetch_updated_ticket_for_reindex = AsyncMock(return_value=None)
        c.pull_requests.check_and_fetch_updated_pr_for_reindex = AsyncMock(return_value=None)

        helper = StreamingHelper(c)
        await helper.reindex_records([ticket, pr])

        c.issues.check_and_fetch_updated_ticket_for_reindex.assert_awaited_once_with(ticket)
        c.pull_requests.check_and_fetch_updated_pr_for_reindex.assert_awaited_once_with(pr)

    async def test_updated_records_are_upserted_not_requeued(self) -> None:
        c = make_mock_connector()
        ticket = _ticket_record()
        fresh_pair = (ticket, [])
        c.issues.check_and_fetch_updated_ticket_for_reindex = AsyncMock(return_value=fresh_pair)

        helper = StreamingHelper(c)
        await helper.reindex_records([ticket])

        c.data_entities_processor.on_new_records.assert_awaited_once_with([fresh_pair])
        c.data_entities_processor.reindex_existing_records.assert_not_awaited()

    async def test_unchanged_records_are_requeued(self) -> None:
        c = make_mock_connector()
        ticket = _ticket_record()
        c.issues.check_and_fetch_updated_ticket_for_reindex = AsyncMock(return_value=None)

        helper = StreamingHelper(c)
        await helper.reindex_records([ticket])

        c.data_entities_processor.reindex_existing_records.assert_awaited_once_with([ticket])

    async def test_empty_list_is_noop(self) -> None:
        c = make_mock_connector()
        helper = StreamingHelper(c)
        await helper.reindex_records([])
        c.runtime.refresh_token_if_needed.assert_not_awaited()
