"""The title an HTML document gives itself, kept apart from the record name."""

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.utils.chat_helpers import get_record, with_document_title


def _title(html: str):
    return HtmlToBlocksConverter().convert(html).document_title


class TestParseTimeTitle:
    def test_og_title_is_preferred_over_a_suffixed_title_tag(self):
        html = (
            '<html><head><title>Release Notes - Example Docs</title>'
            '<meta property="og:title" content="Release Notes"></head><body><p>x</p></body></html>'
        )
        assert _title(html) == "Release Notes"

    def test_title_tag_then_first_h1(self):
        assert _title("<html><head><title> Release\n Notes </title></head><body><p>x</p></body></html>") == (
            "Release Notes"
        )
        assert _title("<html><body><h1>Onboarding</h1><h1>Later</h1><p>x</p></body></html>") == "Onboarding"

    def test_no_title_anywhere(self):
        assert _title("<html><body><p>Just text.</p></body></html>") is None

    def test_merging_containers_keeps_the_first_title(self):
        first = BlocksContainer(document_title="Handbook")
        second = BlocksContainer(
            blocks=[Block(index=0, type=BlockType.TEXT, data="x")], document_title="Other",
        )
        first.extend(second)
        assert first.document_title == "Handbook"
        untitled = BlocksContainer()
        untitled.extend(BlocksContainer(document_title="Handbook"))
        assert untitled.document_title == "Handbook"


class TestRecordHeader:
    HEADER = "Record ID: r1\nName: upload_8f2c.html\nConnector: KB"

    def test_title_is_shown_under_the_record_name(self):
        record = {"record_name": "upload_8f2c.html",
                  "block_containers": {"blocks": [], "document_title": "Release Notes"}}
        assert with_document_title(self.HEADER, record) == (
            "Record ID: r1\nName: upload_8f2c.html\nDocument Title: Release Notes\nConnector: KB"
        )

    def test_title_equal_to_the_name_is_not_repeated(self):
        record = {"record_name": "Release Notes.html",
                  "block_containers": {"document_title": "release notes"}}
        assert with_document_title(self.HEADER, record) == self.HEADER

    def test_records_without_a_title_are_unchanged(self):
        assert with_document_title(self.HEADER, {"record_name": "a.html", "block_containers": {}}) == self.HEADER


class _Store:
    config_service = None

    def __init__(self, record: dict) -> None:
        self.record = record

    async def get_record_from_storage(self, virtual_record_id, org_id, lookup_result=None):
        return dict(self.record)


@pytest.mark.asyncio
async def test_get_record_puts_the_title_in_the_header():
    stored = {"block_containers": {"blocks": [], "block_groups": [], "document_title": "Release Notes"}}
    graph = {"id": "r1", "recordName": "upload_8f2c.html", "recordType": "FILE", "origin": "UPLOAD",
             "connectorName": "KB", "version": 1, "mimeType": "text/html"}
    results: dict = {}

    await get_record("v1", results, _Store(stored), "org-1", {"v1": graph})

    header = results["v1"]["context_metadata"]
    assert "Name: upload_8f2c.html\nDocument Title: Release Notes" in header
