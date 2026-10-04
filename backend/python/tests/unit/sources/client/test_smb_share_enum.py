# ruff: noqa: ANN201
"""NetrShareEnum level-1 decoding. Builds the same layout pysmb reads from Windows."""

from __future__ import annotations

import struct

import pytest

from app.connectors.sources.network_share.errors import ShareListingError
from app.sources.client.smb.share_enum import _read_rpc_pdus, parse_share_enum_response

STYPE_SPECIAL = 0x80000000
STYPE_DISKTREE = 0x00000000
STYPE_PRINTQ = 0x00000001
STYPE_DEVICE = 0x00000002
STYPE_IPC = 0x00000003


def _wstr(text: str) -> bytes:
    encoded = (text + "\x00").encode("utf-16le")
    nchars = len(text) + 1
    pad = b"\x00" * ((4 - (len(encoded) % 4)) % 4)
    return struct.pack("<III", nchars, 0, nchars) + encoded + pad


def _share_enum_pdu(shares: list[tuple[str, int, str | None]], *, status: int = 0) -> bytes:
    stub = bytearray()
    referent = 0x00021000
    fixed = bytearray()
    deferred = bytearray()
    for name, stype, remark in shares:
        name_ref = referent
        referent += 4
        remark_ref = referent if remark is not None else 0
        referent += 4
        fixed.extend(struct.pack("<III", name_ref, stype, remark_ref))
        deferred.extend(_wstr(name))
        if remark is not None:
            deferred.extend(_wstr(remark))
    count = len(shares)
    stub.extend(struct.pack("<I", 1))  # Level
    stub.extend(struct.pack("<I", 1))  # union discriminant
    stub.extend(struct.pack("<I", 0x00020000))  # SHARE_INFO_1_CONTAINER*
    stub.extend(struct.pack("<I", count))
    stub.extend(struct.pack("<I", 0x00020004 if count else 0))
    stub.extend(struct.pack("<I", count))
    stub.extend(fixed)
    stub.extend(deferred)
    stub.extend(struct.pack("<I", count))  # TotalEntries
    stub.extend(struct.pack("<I", 0x00022000))  # ResumeHandle*
    stub.extend(struct.pack("<I", 0))
    stub.extend(struct.pack("<I", status))
    payload = struct.pack("<IHBB", 0, 0, 0, 0) + bytes(stub)
    frag = 16 + len(payload)
    header = bytes([5, 0, 2, 0x03]) + b"\x10\x00\x00\x00" + struct.pack("<HHI", frag, 0, 1)
    return header + payload


def test_parses_names_types_and_ignores_remarks():
    pdu = _share_enum_pdu(
        [
            ("docs", STYPE_DISKTREE, "Default"),
            ("My Share", STYPE_DISKTREE | STYPE_SPECIAL, "Printer"),
            ("queue", STYPE_PRINTQ, "Printer"),
            ("IPC$", STYPE_IPC | STYPE_SPECIAL, "Remote IPC"),
            ("com1", STYPE_DEVICE, "Modem"),
        ]
    )
    shares = parse_share_enum_response(pdu)
    assert [(share.name, share.share_type) for share in shares] == [
        ("docs", "disk"),
        ("My Share", "disk"),
        ("queue", "print"),
        ("IPC$", "ipc"),
        ("com1", "device"),
    ]


def test_special_type_dword_is_not_a_share_name():
    pdu = _share_enum_pdu([("C$", STYPE_DISKTREE | STYPE_SPECIAL, "Default")])
    shares = parse_share_enum_response(pdu)
    assert [share.name for share in shares] == ["C$"]
    assert "\u8000" not in {share.name for share in shares}


def test_null_remark_is_skipped():
    pdu = _share_enum_pdu([("docs", STYPE_DISKTREE, None), ("archive", STYPE_DISKTREE, "files")])
    shares = parse_share_enum_response(pdu)
    assert [share.name for share in shares] == ["docs", "archive"]


def test_structural_failure_raises():
    pdu = bytearray(_share_enum_pdu([("docs", STYPE_DISKTREE, "ok")]))
    pdu[24:28] = struct.pack("<I", 2)  # Level is not SHARE_INFO_1
    with pytest.raises(ShareListingError):
        parse_share_enum_response(bytes(pdu))


def test_partial_enum_raises():
    pdu = _share_enum_pdu([("docs", STYPE_DISKTREE, "ok")], status=234)
    with pytest.raises(ShareListingError, match="partial"):
        parse_share_enum_response(pdu)


def test_empty_enum_raises():
    with pytest.raises(ShareListingError):
        parse_share_enum_response(_share_enum_pdu([]))


class _Pipe:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.reads = 0

    def read(self, _size: int) -> bytes:
        self.reads += 1
        if not self._chunks:
            raise AssertionError("read past the last fragment")
        return self._chunks.pop(0)


def _split_response(pdu: bytes) -> tuple[bytes, bytes]:
    stub = pdu[24:]
    mid = max(1, len(stub) // 2)
    response_header = pdu[16:24]

    def _frag(payload: bytes, flags: int) -> bytes:
        frag_len = 24 + len(payload)
        common = bytes([5, 0, 2, flags]) + b"\x10\x00\x00\x00" + struct.pack("<HHI", frag_len, 0, 1)
        return common + response_header + payload

    return _frag(stub[:mid], 0x01), _frag(stub[mid:], 0x02)


def test_fragment_reader_stops_on_the_fragment_that_has_last_flag():
    pdu = _share_enum_pdu([("docs", STYPE_DISKTREE, "Default"), ("archive", STYPE_DISKTREE, "files")])
    first, second = _split_response(pdu)
    pipe = _Pipe([first[:10], first[10:] + second])
    assembled = _read_rpc_pdus(pipe)
    assert pipe.reads == 2
    assert [(share.name, share.share_type) for share in parse_share_enum_response(assembled)] == [
        ("docs", "disk"),
        ("archive", "disk"),
    ]


def test_fragment_reader_rejects_a_short_read():
    pipe = _Pipe([b"\x05\x00\x02\x00", b""])
    with pytest.raises(ShareListingError):
        _read_rpc_pdus(pipe)
    assert pipe.reads == 2
