"""Every connector's new .log/.cfg is saved under its own mime, whatever mime the connector sent
(a desktop stamp, a guess_type text/plain, octet-stream), so it is read as capped text; a row
already stored as text/plain keeps it (D-16)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config import file_types
from app.config.constants.arangodb import MimeTypes
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
)

_OWN = {"log": "text/x-log", "cfg": "text/x-cfg"}


def _stored(tx_store: AsyncMock) -> list[object]:
    return [r for c in tx_store.batch_upsert_records.await_args_list for r in c.args[0]]


@pytest.mark.parametrize("ext", ["log", "cfg"])
@pytest.mark.parametrize("sent", ["text/plain", "application/octet-stream", MimeTypes.UNKNOWN.value, "TEXT/PLAIN"])
async def test_a_new_file_is_saved_under_its_own_mime(ext: str, sent: str) -> None:
    proc, tx_store = _make_processor(), _make_tx_store()
    proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
    await proc.on_new_records([(_make_record(record_name=f"server.{ext}", mime_type=sent), [])])
    assert [r.mime_type for r in _stored(tx_store)] == [_OWN[ext]]


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_a_row_stored_as_text_plain_keeps_it_on_the_next_sync(ext: str) -> None:
    proc, tx_store = _make_processor(), _make_tx_store()
    stored = _make_record(record_name=f"server.{ext}", mime_type="text/plain")
    tx_store.get_record_by_external_id = AsyncMock(return_value=stored)
    proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
    await proc.on_new_records([(_make_record(record_name=f"server.{ext}", mime_type="text/plain"), [])])
    assert [r.mime_type for r in _stored(tx_store)] == ["text/plain"]


@pytest.mark.parametrize(
    ("name", "sent"),
    [("notes.txt", "text/plain"), ("data.json", "text/plain"), ("a.log", "application/json"), ("a.pdf", "application/pdf")],
)
async def test_every_other_file_keeps_the_mime_its_connector_sent(name: str, sent: str) -> None:
    proc, tx_store = _make_processor(), _make_tx_store()
    proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
    await proc.on_new_records([(_make_record(record_name=name, mime_type=sent), [])])
    assert [r.mime_type for r in _stored(tx_store)] == [sent]


def test_the_shared_rule() -> None:
    assert file_types.stored_mime_for_new_file("text/plain", "a.LOG") == "text/x-log"
    assert file_types.stored_mime_for_new_file(None, "a.cfg") == "text/x-cfg"
    assert file_types.stored_mime_for_new_file("text/plain", "a.txt") == "text/plain"
    assert file_types.stored_mime_for_new_file("application/json", "a.log") == "application/json"
    assert file_types.stored_mime_for_new_file("text/plain", "a.docm") == "text/plain"
    assert file_types.stored_mime_for_new_file("text/plain", None) == "text/plain"
