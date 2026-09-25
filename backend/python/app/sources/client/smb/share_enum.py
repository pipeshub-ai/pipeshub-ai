"""NetrShareEnum (MS-SRVS opnum 15) over IPC$ using smbprotocol/smbclient.

Written from the public MS-RPCE and MS-SRVS layouts. A failure here is not
fatal: callers fall back to the configured share name.
"""

from __future__ import annotations

import struct
import uuid
from typing import Any

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


def parse_share_enum_response(data: bytes) -> list[ShareInfo]:
    if len(data) < 16:
        raise ShareListingError("NetrShareEnum response too short")
    ptype = data[2]
    if ptype not in (RESPONSE_PTYPE, 3):  # 3 = fault
        raise ShareListingError(f"Unexpected RPC ptype {ptype}")
    if ptype == 3:
        raise ShareListingError("RPC fault from NetrShareEnum")
    stub = data[24:] if len(data) > 24 else data[16:]
    # Collect UTF-16LE strings of plausible share names plus a following DWORD type.
    # NDR layout for SHARE_INFO_1 is pointer, type, pointer; names live in the
    # deferred referent array. Pull null-terminated UTF-16 strings and skip IPC.
    names: list[str] = []
    decoded = stub.decode("utf-16le", errors="ignore")
    current: list[str] = []
    for ch in decoded:
        if ch == "\x00":
            token = "".join(current)
            current = []
            if token and all(c.isalnum() or c in ("$", "-", "_", ".") for c in token) and len(token) <= 80:
                names.append(token)
        else:
            current.append(ch)
    # Pairing names with types is unreliable from a string scan. Treat unknown
    # as disk; callers drop IPC$ / ADMIN$.
    shares: list[ShareInfo] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        kind = "ipc" if name.upper() == "IPC$" else "disk"
        shares.append(ShareInfo(name=name, share_type=kind))
    if not shares:
        raise ShareListingError("NetrShareEnum returned no share names")
    return shares


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
            chunks = [pipe.read(8192)]
            data = b"".join(chunk for chunk in chunks if chunk)
            if not data:
                raise ShareListingError("Empty NetrShareEnum response")
            # Continue reading while LAST_FRAG is unset
            while data and (data[3] & PFC_LAST) == 0:
                more = pipe.read(8192)
                if not more:
                    break
                data += more
    except ShareListingError:
        raise
    except Exception as exc:
        raise ShareListingError(str(exc)) from exc
    return parse_share_enum_response(data)
