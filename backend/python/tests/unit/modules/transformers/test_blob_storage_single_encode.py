"""A stored record is JSON-encoded once, and the bytes written do not change.

The stdlib encoder holds the GIL for the whole call, so each extra pass over a
large record is time the indexing event loop cannot run. Uploads used to
encode the record once to size it for the compression threshold and again to
build the envelope (a third time on the cloud path, to report its size).
"""

import json
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    _ascii_json_size,
    _encode_record_json,
    _json_utf8_bytes,
    _plain_envelope_bytes,
)

MODULE = "app.modules.transformers.blob_storage"

_ALPHABET = (
    "abc XYZ 019 \"\\/\x00\x01\x1f\t\n\r\b\f\x7f\x80éú߿ࠀ京"
    "﻿￿\U00010000\U0001f600\U0010ffff"
)


def _random_text(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(n))


def _records() -> list:
    rng = random.Random(3)
    records: list = [
        {},
        {"plain": "ascii only", "n": 1, "f": 1.5, "t": True, "none": None},
        {"del": "\x7f\x7f", "nan": float("nan"), "inf": float("-inf")},
        {"text": "Borgartún 27 · 北京 · Москва · 😀"},
        {"broken": "lone \ud800 surrogate", "also": "北京"},
        {1: "int key", "nested": [{"k": [" ", "\x7f", "😀"]}]},
    ]
    records.extend(
        {
            _random_text(rng, 5): [_random_text(rng, rng.randint(0, 60)) for _ in range(5)],
            "blocks": [{"data": _random_text(rng, 30), "index": i} for i in range(5)],
        }
        for _ in range(40)
    )
    return records


@pytest.mark.parametrize("record", _records())
def test_ascii_size_matches_the_ascii_encoding(record) -> None:
    encoded = _encode_record_json(record)
    assert encoded is not None
    assert encoded.ascii_size == len(json.dumps(record).encode("utf-8"))
    assert encoded.body == _json_utf8_bytes(record)


def test_ascii_size_of_every_character_class() -> None:
    for ch in _ALPHABET + "퟿":
        assert _ascii_json_size(json.dumps(ch, ensure_ascii=False).encode()) == len(json.dumps(ch)), repr(ch)


@pytest.mark.parametrize("record", _records())
@pytest.mark.parametrize("vrid", ["vr-1", None, "vr-ü", "vr-\udc00"])
def test_plain_envelope_bytes_are_unchanged(record, vrid) -> None:
    envelope = {"isCompressed": False, "record": record, "virtualRecordId": vrid}
    assert _plain_envelope_bytes(record, _encode_record_json(record), vrid) == _json_utf8_bytes(envelope)
    assert _plain_envelope_bytes(record, None, vrid) == _json_utf8_bytes(envelope)


def test_unserializable_record_has_no_encoding() -> None:
    assert _encode_record_json({"when": {1, 2}}) is None


# ---------------------------------------------------------------------------
# Upload paths
# ---------------------------------------------------------------------------


def _big_record() -> dict:
    rng = random.Random(9)
    return {
        "id": "rec-1",
        "block_containers": {
            "blocks": [{"index": i, "data": _random_text(rng, 80)} for i in range(400)],
        },
    }


class _CountingJson:
    """Counts json.dumps calls whose output is at least as long as the record."""

    def __init__(self, min_len: int) -> None:
        self.min_len = min_len
        self.large = 0
        self._dumps = json.dumps

    def dumps(self, obj: object, *args: object, **kwargs: object) -> str:
        out = self._dumps(obj, *args, **kwargs)
        if len(out) >= self.min_len:
            self.large += 1
        return out

    def __getattr__(self, name: str) -> object:
        return getattr(json, name)


def _make_bs(storage_type: str) -> BlobStorage:
    bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    bs._get_auth_and_config = AsyncMock(
        return_value=({"Authorization": "Bearer t"}, "http://node:3000", storage_type)
    )
    return bs


def _resp(status: int = 200, body: dict | None = None) -> AsyncMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body or {"_id": "doc-1"})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.fixture(autouse=True)
def _default_threshold(monkeypatch) -> None:
    monkeypatch.delenv(bs_mod._COMPRESSION_THRESHOLD_ENV, raising=False)


@pytest.mark.asyncio
async def test_update_record_buffer_encodes_the_record_once() -> None:
    record = _big_record()
    counter = _CountingJson(len(json.dumps(record, ensure_ascii=False)))
    form = MagicMock()
    session = MagicMock()
    session.put = MagicMock(return_value=_resp())

    with patch(f"{MODULE}.json", counter), \
         patch(f"{MODULE}.get_shared_session", return_value=session), \
         patch(f"{MODULE}.aiohttp.FormData", return_value=form):
        _, size = await _make_bs("local").update_record_buffer("org-1", "doc-1", record, "vr-1")

    [file_call] = [c for c in form.add_field.call_args_list if c.args[0] == "file"]
    expected = _json_utf8_bytes({"isCompressed": False, "record": record, "virtualRecordId": "vr-1"})
    assert file_call.args[1] == expected
    assert size == len(expected)
    assert counter.large == 1


@pytest.mark.asyncio
async def test_local_save_encodes_the_record_once() -> None:
    record = _big_record()
    counter = _CountingJson(len(json.dumps(record, ensure_ascii=False)))
    session = MagicMock()
    session.post = MagicMock(return_value=_resp())
    forms = []

    def _form(json_data: bytes, *args: object, **kwargs: object) -> MagicMock:
        forms.append(json_data)
        return MagicMock()

    with patch(f"{MODULE}.json", counter), \
         patch(f"{MODULE}.get_shared_session", return_value=session), \
         patch(f"{MODULE}._versioned_json_form", side_effect=_form):
        _, size = await _make_bs("local").save_record_to_storage("org-1", "rec-1", "vr-1", record)

    expected = _json_utf8_bytes({"isCompressed": False, "record": record, "virtualRecordId": "vr-1"})
    assert forms == [expected]
    assert size == len(expected)
    assert counter.large == 1


@pytest.mark.asyncio
async def test_cloud_save_reports_the_size_it_sends_without_reencoding() -> None:
    record = _big_record()
    counter = _CountingJson(len(json.dumps(record, ensure_ascii=False)))
    bs = _make_bs("s3")
    bs._create_placeholder = AsyncMock(return_value={"_id": "doc-1"})
    bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://bucket/key"})
    bs._upload_to_signed_url = AsyncMock(return_value=200)

    with patch(f"{MODULE}.json", counter), patch(f"{MODULE}.get_shared_session", return_value=MagicMock()):
        _, size = await bs.save_record_to_storage("org-1", "rec-1", "vr-1", record)

    sent = bs._upload_to_signed_url.await_args.args[2]
    assert sent == _json_utf8_bytes({"isCompressed": False, "record": record, "virtualRecordId": "vr-1"})
    assert size == len(sent)
    assert counter.large == 1


@pytest.mark.asyncio
async def test_compression_boundary_is_measured_as_before(monkeypatch) -> None:
    """The threshold still measures the ASCII-escaped record, non-ASCII included."""
    record = {"text": "北京 😀 Borgartún \x7f" * 50}
    ascii_size = len(json.dumps(record).encode("utf-8"))
    assert ascii_size > len(json.dumps(record, ensure_ascii=False).encode("utf-8"))

    for threshold, compressed in ((ascii_size, False), (ascii_size - 1, True)):
        monkeypatch.setenv(bs_mod._COMPRESSION_THRESHOLD_ENV, str(threshold))
        session = MagicMock()
        session.post = MagicMock(return_value=_resp())
        form = MagicMock(return_value=MagicMock())
        with patch(f"{MODULE}.get_shared_session", return_value=session), \
             patch(f"{MODULE}._versioned_json_form", form):
            await _make_bs("local").save_record_to_storage("org-1", "rec-1", "vr-1", record)
        assert form.call_args.kwargs["compressed"] is compressed
        assert json.loads(form.call_args.args[0])["isCompressed"] is compressed
