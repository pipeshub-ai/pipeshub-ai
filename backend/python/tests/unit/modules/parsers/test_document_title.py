"""The title an HTML document gives itself, kept beside the record's name."""

from typing import Any

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.utils.chat_helpers import get_record, with_document_title


@pytest.fixture
def section_paths_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEXING_SECTION_PATHS", "true")


def _title(html: str) -> str | None:
    return HtmlToBlocksConverter().convert(html).document_title


@pytest.mark.usefixtures("section_paths_on")
class TestParseTimeTitle:
    def test_og_title_is_preferred_over_a_suffixed_title_tag(self) -> None:
        html = (
            '<html><head><title>Pruning Calendar - Orchard Wiki</title>'
            '<meta property="og:title" content="Pruning Calendar"></head><body><p>x</p></body></html>'
        )
        assert _title(html) == "Pruning Calendar"

    def test_title_tag_then_first_h1(self) -> None:
        assert _title("<html><head><title> Pruning\n Calendar </title></head><body><p>x</p></body></html>") == (
            "Pruning Calendar"
        )
        assert _title("<html><body><h1>Grafting</h1><h1>Later</h1><p>x</p></body></html>") == "Grafting"

    def test_no_title_anywhere(self) -> None:
        assert _title("<html><body><p>Just text.</p></body></html>") is None


def test_switched_off_no_title_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_SECTION_PATHS", raising=False)
    assert _title("<html><head><title>Pruning Calendar</title></head><body><p>x</p></body></html>") is None


def test_merging_containers_keeps_the_first_title() -> None:
    first = BlocksContainer(document_title="Orchard Log")
    second = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, data="x")], document_title="Other",
    )
    first.extend(second)
    assert first.document_title == "Orchard Log"
    untitled = BlocksContainer()
    untitled.extend(BlocksContainer(document_title="Orchard Log"))
    assert untitled.document_title == "Orchard Log"


class TestRecordHeader:
    HEADER = "Record ID: r1\nName: upload_3c9d.html\nConnector: KB"

    def test_title_is_shown_under_the_record_name(self) -> None:
        record = {"record_name": "upload_3c9d.html",
                  "block_containers": {"blocks": [], "document_title": "Pruning Calendar"}}
        assert with_document_title(self.HEADER, record) == (
            "Record ID: r1\nName: upload_3c9d.html\nDocument Title: Pruning Calendar\nConnector: KB"
        )

    def test_title_equal_to_the_name_is_not_repeated(self) -> None:
        record = {"record_name": "Pruning Calendar.html",
                  "block_containers": {"document_title": "pruning calendar"}}
        assert with_document_title(self.HEADER, record) == self.HEADER

    def test_records_without_a_title_are_unchanged(self) -> None:
        assert with_document_title(self.HEADER, {"record_name": "a.html", "block_containers": {}}) == self.HEADER


class _Store:
    config_service = None

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    async def get_record_from_storage(
        self, virtual_record_id: str, org_id: str, lookup_result: Any = None,  # noqa: ANN401
    ) -> dict[str, Any]:
        return dict(self.record)


@pytest.mark.asyncio
async def test_get_record_puts_the_title_in_the_header() -> None:
    stored = {"block_containers": {"blocks": [], "block_groups": [], "document_title": "Pruning Calendar"}}
    graph = {"id": "r1", "recordName": "upload_3c9d.html", "recordType": "FILE", "origin": "UPLOAD",
             "connectorName": "KB", "version": 1, "mimeType": "text/html"}
    results: dict = {}

    await get_record("v1", results, _Store(stored), "org-1", {"v1": graph})

    assert "Name: upload_3c9d.html\nDocument Title: Pruning Calendar" in results["v1"]["context_metadata"]
