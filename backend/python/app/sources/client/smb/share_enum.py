"""NetrShareEnum (MS-SRVS opnum 15) over IPC$ using smbprotocol/smbclient.

Written from the public MS-RPCE and MS-SRVS layouts. A failure here is not
fatal: callers fall back to the configured share name.
"""

from __future__ import annotations

import struct
import uuid
from typing import Any, Protocol

from app.connectors.sources.network_share.entry import ShareInfo
from app.connectors.sources.network_share.errors import ShareListingError

SRVSVC_UUID = uuid.UUID("4b324fc8-1670-01d3-1278-5a47bf6ee188")
NDR_UUID = uuid.UUID("8a885d04-1ceb-11c9-9fe8-08002b104860")
BIND_PTYPE = 11
REQUEST_PTYPE = 0
BIND_ACK_PTYPE = 12
RESPONSE_PTYPE = 2
PFC_FIRST = 0x01
PFC_LAST = 0x02
NETR_SHARE_ENUM = 15
STYPE_DISKTREE = 0x00000000
STYPE_PRINTQ = 0x00000001
STYPE_DEVICE = 0x00000002
STYPE_IPC = 0x00000003


def _rpc_uuid(value: uuid.UUID) -> bytes:
    return value.bytes_le


def bind_pdu(call_id: int = 1) -> bytes:
    abstract = _rpc_uuid(SRVSVC_UUID) + struct.pack("<HH", 3, 0)
    transfer = _rpc_uuid(NDR_UUID) + struct.pack("<HH", 2, 0)
    context = struct.pack("<HBB", 0, 1, 0) + abstract + transfer
    extra = struct.pack("<HHLBxxx", 4280, 4280, 0, 1) + context
    payload = b""
    flags = PFC_FIRST | PFC_LAST
    body = extra + payload
    frag = 16 + len(body)
    header = bytes([5, 0, BIND_PTYPE, flags]) + b"\x10\x00\x00\x00" + struct.pack(
        "<HHL", frag, 0, call_id
    )
    return header + body


def _conformant_string(text: str, referent: int) -> bytes:
    encoded = (text + "\x00").encode("utf-16le")
    nchars = len(text) + 1
    return (
        struct.pack("<I", referent)
        + struct.pack("<III", nchars, 0, nchars)
        + encoded
        + (b"\x00\x00" if len(encoded) % 4 else b"")
    )


def netr_share_enum_pdu(server: str, call_id: int = 2) -> bytes:
    # ServerName unique pointer + wchar string, Level=1, empty enum union, max length, resume=0
    stub = b""
    stub += _conformant_string(f"\\\\{server}", 0x00020000)
    stub += struct.pack("<I", 1)  # Level
    stub += struct.pack("<I", 1)  # union discriminant Level
    stub += struct.pack("<I", 0)  # SHARE_INFO_1_CONTAINER* null
    stub += struct.pack("<I", 0xFFFFFFFF)  # PreferedMaximumLength
    stub += struct.pack("<I", 0x00020004)  # ResumeHandle unique
    stub += struct.pack("<I", 0)  # resume value 0
    extra = struct.pack("<LHH", 0, 0, NETR_SHARE_ENUM)  # alloc_hint, context_id, opnum
    flags = PFC_FIRST | PFC_LAST
    body = extra + stub
    frag = 16 + len(body)
    header = bytes([5, 0, REQUEST_PTYPE, flags]) + b"\x10\x00\x00\x00" + struct.pack(
        "<HHL", frag, 0, call_id
    )
    return header + body


def _share_kind(stype: int) -> str:
    kind = stype & 0x0000000F
    return {STYPE_DISKTREE: "disk", STYPE_PRINTQ: "print", STYPE_DEVICE: "device", STYPE_IPC: "ipc"}.get(
        kind, "disk"
    )


def _response_stub(data: bytes) -> bytes:
    if len(data) < 24:
        raise ShareListingError("NetrShareEnum response too short")
    stub = bytearray()
    offset = 0
    saw_last = False
    while offset + 16 <= len(data):
        ptype = data[offset + 2]
        flags = data[offset + 3]
        frag_len = struct.unpack_from("<H", data, offset + 8)[0]
        auth_len = struct.unpack_from("<H", data, offset + 10)[0]
        if frag_len < 24 or offset + frag_len > len(data):
            raise ShareListingError("Truncated NetrShareEnum response")
        if ptype == 3:
            raise ShareListingError("RPC fault from NetrShareEnum")
        if ptype != RESPONSE_PTYPE:
            raise ShareListingError(f"Unexpected RPC ptype {ptype}")
        stub_end = offset + frag_len - auth_len
        if stub_end < offset + 24:
            raise ShareListingError("Truncated NetrShareEnum response")
        stub.extend(data[offset + 24:stub_end])
        offset += frag_len
        if flags & PFC_LAST:
            saw_last = True
            break
    if not saw_last:
        raise ShareListingError("Truncated NetrShareEnum response")
    return bytes(stub)


def _u32(buf: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(buf):
        raise ShareListingError("Truncated NetrShareEnum response")
    return struct.unpack_from("<I", buf, offset)[0], offset + 4


def _read_conformant_string(buf: bytes, offset: int) -> tuple[str, int]:
    max_count, offset = _u32(buf, offset)
    varying_offset, offset = _u32(buf, offset)
    actual, offset = _u32(buf, offset)
    if varying_offset != 0 or actual == 0 or actual > max_count or actual > 256:
        raise ShareListingError("Invalid NetrShareEnum string")
    nbytes = actual * 2
    if offset + nbytes > len(buf):
        raise ShareListingError("Truncated NetrShareEnum response")
    raw = buf[offset:offset + nbytes]
    offset += nbytes
    pad = (4 - (nbytes % 4)) % 4
    if pad and offset + pad <= len(buf):
        offset += pad
    try:
        text = raw.decode("utf-16le").rstrip("\x00")
    except UnicodeDecodeError as exc:
        raise ShareListingError("Invalid NetrShareEnum string") from exc
    return text, offset


def parse_share_enum_response(data: bytes) -> list[ShareInfo]:
    """Decode a NetrShareEnum level-1 response.

    SHARE_INFO_1 on the wire is a fixed (netname pointer, type, remark pointer)
    array, then the deferred wchar strings in that same order. Remarks and the
    type DWORD are not share names. A structural failure raises ShareListingError
    so the caller can fall back to the configured share.
    """
    offset = 0
    stub = _response_stub(data)
    level, offset = _u32(stub, offset)
    discriminant, offset = _u32(stub, offset)
    if level != 1 or discriminant != 1:
        raise ShareListingError("NetrShareEnum response was not SHARE_INFO_1")
    container, offset = _u32(stub, offset)
    if container == 0:
        raise ShareListingError("NetrShareEnum returned no share names")
    count, offset = _u32(stub, offset)
    if count == 0:
        raise ShareListingError("NetrShareEnum returned no share names")
    if count > 4096:
        raise ShareListingError("NetrShareEnum entry count is not plausible")
    buffer_ptr, offset = _u32(stub, offset)
    max_count, offset = _u32(stub, offset)
    if buffer_ptr == 0 or max_count != count:
        raise ShareListingError("NetrShareEnum conformant array does not match EntriesRead")

    fixed: list[tuple[int, int, int]] = []
    for _ in range(count):
        name_ptr, offset = _u32(stub, offset)
        stype, offset = _u32(stub, offset)
        remark_ptr, offset = _u32(stub, offset)
        fixed.append((name_ptr, stype, remark_ptr))

    shares: list[ShareInfo] = []
    seen: set[str] = set()
    for name_ptr, stype, remark_ptr in fixed:
        if name_ptr == 0:
            raise ShareListingError("NetrShareEnum entry is missing a share name")
        name, offset = _read_conformant_string(stub, offset)
        if remark_ptr:
            _, offset = _read_conformant_string(stub, offset)
        if not name:
            raise ShareListingError("NetrShareEnum entry is missing a share name")
        if name in seen:
            continue
        seen.add(name)
        shares.append(ShareInfo(name=name, share_type=_share_kind(stype)))
    if not shares:
        raise ShareListingError("NetrShareEnum returned no share names")

    _total, offset = _u32(stub, offset)
    resume_ptr, offset = _u32(stub, offset)
    if resume_ptr:
        _, offset = _u32(stub, offset)
    status, _offset = _u32(stub, offset)
    if status == 234:  # ERROR_MORE_DATA: a partial list must not be treated as complete
        raise ShareListingError("NetrShareEnum returned a partial share list")
    if status != 0:
        raise ShareListingError(f"NetrShareEnum failed with status {status}")
    return shares


class _RpcPipe(Protocol):
    def read(self, size: int) -> bytes: ...


def _read_rpc_pdus(pipe: _RpcPipe) -> bytes:
    """Read response fragments and return them concatenated, headers included.

    smbclient's pipe read issues one SMB2 Read and returns that payload. On a
    message-mode pipe that is one message, which may be one fragment or only
    part of one, and a further read blocks until the server writes again.
    Stop on the fragment whose own flags include PFC_LAST.
    """
    buf = b""
    pdus = bytearray()
    while True:
        while len(buf) < 16:
            chunk = pipe.read(8192)
            if not chunk:
                raise ShareListingError("Truncated NetrShareEnum response")
            buf += chunk
        frag_len = struct.unpack_from("<H", buf, 8)[0]
        if frag_len < 24 or frag_len > 1024 * 1024:
            raise ShareListingError("Invalid NetrShareEnum fragment length")
        while len(buf) < frag_len:
            chunk = pipe.read(8192)
            if not chunk:
                raise ShareListingError("Truncated NetrShareEnum fragment")
            buf += chunk
        frag = buf[:frag_len]
        buf = buf[frag_len:]
        pdus.extend(frag)
        if frag[3] & PFC_LAST:
            return bytes(pdus)


def enumerate_shares(
    server: str,
    connection_cache: dict[str, Any],
    port: int = 445,
) -> list[ShareInfo]:
    try:
        import smbclient
    except ImportError as exc:
        raise ShareListingError("smbprotocol is not installed") from exc

    path = rf"\\{server}\IPC$\srvsvc"
    try:
        with smbclient.open_file(
            path,
            mode="r+b",
            buffering=0,
            file_type="pipe",
            share_access="rw",
            port=port,
            connection_cache=connection_cache,
        ) as pipe:
            pipe.write(bind_pdu())
            ack = pipe.read(4096)
            if not ack or (len(ack) > 2 and ack[2] not in (BIND_ACK_PTYPE, BIND_PTYPE)):
                raise ShareListingError("RPC bind to srvsvc failed")
            pipe.write(netr_share_enum_pdu(server))
            data = _read_rpc_pdus(pipe)
    except ShareListingError:
        raise
    except Exception as exc:
        raise ShareListingError(str(exc)) from exc
    return parse_share_enum_response(data)
