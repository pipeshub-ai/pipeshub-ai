"""With ``USE_PARSING_SERVICE``, the parse request carries the stored version's table rows, so the parsing service keeps an
unchanged row's text as indexing does.

Before, the parsing service described every table row of an updated PDF again: the offer of stored rows lived only in the
indexing process. All data is synthetic.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.parsing.client import ParsingClient
from app.services.parsing.interface import STORED_ROWS_PART
from app.services.parsing.registry import ParserRegistry
from app.utils import table_row_memory
from app.utils.table_row_memory import (
    PreviousRows,
    decode_stored_tables,
    encode_stored_tables,
    end_previous_rows,
    offer_previous_rows,
    previous_row_texts,
)
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

LOG = logging.getLogger("test-parse-stored-rows")
TABLES = [{"d" * 32: ["Warehouse W-03 in Derby has 41 staff."], "e" * 32: ["First total.", "Second total."]}, {"f" * 32: ["Q2."]}]


@pytest.fixture(autouse=True)
def _no_offer() -> Any:  # noqa: ANN401
    end_previous_rows()
    yield
    end_previous_rows()


class TestTheWireFormat:
    def test_tables_come_back_as_they_were_sent(self) -> None:
        raw = encode_stored_tables(TABLES)
        assert raw is not None and decode_stored_tables(raw) == TABLES

    @pytest.mark.parametrize(
        "raw",
        [b"not json", b'{"d": ["t"]}', b'[{"d": "t"}]', b'[{"d": [1]}]', b"[[]]", b'[{"d": ["\xff"]}]'],
        ids=["not-json", "not-a-list", "text-not-a-list", "number-text", "table-not-a-dict", "bad-utf8"],
    )
    def test_anything_else_is_refused(self, raw: bytes) -> None:
        assert decode_stored_tables(raw) is None

    def test_rows_over_the_cap_are_neither_sent_nor_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        raw = encode_stored_tables(TABLES)
        assert raw is not None
        monkeypatch.setattr(table_row_memory, "MAX_SENT_ROW_BYTES", len(raw) - 1)
        assert encode_stored_tables(TABLES) is None
        assert decode_stored_tables(raw) is None


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"success": True, "block_container": {"blocks": [], "block_groups": []}, "provider_used": "default"})


async def _sent_files() -> dict[str, Any]:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    post = AsyncMock(return_value=_ok_response())
    with patch.object(client, "_post_multipart", new=post):
        await client.parse(file_content=b"%PDF", record_name="w.pdf", mime_type="application/pdf", extension="pdf")
    return post.await_args.kwargs["files"]


class TestTheClient:
    @pytest.mark.asyncio
    async def test_an_update_sends_the_stored_rows_with_the_file(self) -> None:
        # Kills: the parse request leaving the stored rows behind, so the parsing service describes every row again.
        offer_previous_rows(PreviousRows.already_read(TABLES, LOG, "rec-1"))
        files = await _sent_files()
        name, raw, content_type = files[STORED_ROWS_PART]
        assert (name, content_type) == ("stored_rows.json", "application/json")
        assert decode_stored_tables(raw) == TABLES

    @pytest.mark.asyncio
    async def test_a_delivery_that_offered_none_sends_only_the_file(self) -> None:
        assert list(await _sent_files()) == ["file"]
        offer_previous_rows(PreviousRows.already_read([], LOG, "rec-1"))
        assert list(await _sent_files()) == ["file"]

    @pytest.mark.asyncio
    async def test_rows_too_large_to_send_are_left_out_with_one_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(table_row_memory, "MAX_SENT_ROW_BYTES", 10)
        offer_previous_rows(PreviousRows.already_read(TABLES, LOG, "rec-1"))
        with caplog.at_level(logging.WARNING):
            assert list(await _sent_files()) == ["file"]
        assert sum("too large to send" in r.getMessage() for r in caplog.records) == 1
        assert "Derby" not in caplog.text


def _post(seen: list[Any], stored: bytes | None) -> int:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        seen.append(await previous_row_texts())
        return _ok_result()

    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=MagicMock(parse=parse))
    files: dict[str, Any] = {"file": ("w.pdf", b"%PDF-1.4", "application/pdf")}
    if stored is not None:
        files[STORED_ROWS_PART] = ("stored_rows.json", stored, "application/json")
    response = TestClient(_build_app(registry)).post(
        "/api/v1/parse",
        files=files,
        data={"record_name": "w.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    )
    return response.status_code


class TestTheRoute:
    def test_the_parse_sees_the_rows_the_request_carried(self) -> None:
        seen: list[Any] = []
        assert _post(seen, encode_stored_tables(TABLES)) == 200
        assert seen == [TABLES]

    def test_a_request_without_rows_offers_none_even_after_one_with_rows(self) -> None:
        # An offer must never outlive its request: another record's parse would keep this record's row text.
        seen: list[Any] = []
        assert _post(seen, encode_stored_tables(TABLES)) == 200
        assert _post(seen, None) == 200
        assert seen == [TABLES, None]

    def test_unreadable_rows_are_ignored_and_the_file_is_still_parsed(self, caplog: pytest.LogCaptureFixture) -> None:
        seen: list[Any] = []
        with caplog.at_level(logging.WARNING):
            assert _post(seen, json.dumps({"d": "not rows"}).encode()) == 200
        assert seen == [None]
        assert any("unreadable or too large" in r.getMessage() for r in caplog.records)
