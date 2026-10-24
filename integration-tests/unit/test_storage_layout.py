"""The storage-layout probe's pure parts, offline.

``expected_segment`` is the folder-naming rule written out independently of the
app; the comparison below is what makes a change to either side visible.
"""

from __future__ import annotations

import base64
import json

import msgspec
import pytest
import zstandard

from helper.storage_layout import (
    MAX_SEGMENT_BYTES,
    StoredCopy,
    _copy_from,
    decode_stored_record,
    expected_path,
    expected_segment,
    oversized_segments,
)

pytestmark = pytest.mark.unit

NAMES = [
    "plan",
    "Q3 2026",
    'Plans: "draft" <v2>',
    "a/b\\c",
    "  padded  ",
    "",
    "   ",
    ".",
    "..",
    "Q1..Q2",
    "tab\there",
    "報告書" * 40,
    "😀" * 80,
    "x" * 150,
    "é" * 130,
]


@pytest.mark.parametrize("name", NAMES, ids=range(len(NAMES)))
def test_the_written_out_rule_matches_the_app(name):
    from app.utils.storage_path import sanitize_path_segment

    assert expected_segment(name) == sanitize_path_segment(name)


@pytest.mark.parametrize("name", NAMES, ids=range(len(NAMES)))
def test_every_segment_fits_a_file_name(name):
    assert len(expected_segment(name).encode()) <= MAX_SEGMENT_BYTES


def test_a_name_that_fits_is_kept_byte_for_byte():
    assert expected_segment("Board Reports") == "Board Reports"


def test_expected_path_starts_at_the_connector():
    assert expected_path("kb1", "A", "b") == "records/kb1/A/b"


def test_expected_path_refuses_a_chain_over_the_cap():
    with pytest.raises(AssertionError):
        expected_path("kb1", *(["d" * 99] * 10))


def test_stored_documents_parse_into_copies():
    copy = _copy_from(
        {"_id": "abc", "documentName": "record_v1", "documentPath": "org/PipesHub/records/c/A"}, "org"
    )
    assert copy == StoredCopy("record", "v1", "abc", "records/c/A")
    assert copy.file == "records/c/A/abc/record_v1.json"


@pytest.mark.parametrize("name", ["upload", "recordv1", "summary_v1", ""])
def test_other_documents_are_not_stored_records(name):
    assert _copy_from({"_id": "x", "documentName": name, "documentPath": "org/PipesHub/x"}, "org") is None


def test_oversized_segments_are_reported():
    assert oversized_segments("records/c/" + "a" * 256 + "/ok") == ["a" * 256]


def test_plain_and_compressed_records_decode_alike():
    record = {"id": "r1", "blocks": [{"text": "hello"}]}
    plain = json.dumps(record).encode()
    packed = base64.b64encode(zstandard.ZstdCompressor().compress(msgspec.msgpack.encode(record))).decode()
    compressed = json.dumps({"isCompressed": True, "record": packed}).encode()
    assert decode_stored_record(plain) == decode_stored_record(compressed) == record
