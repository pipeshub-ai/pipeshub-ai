"""CVE-2026-8838: redshift_connector < 2.1.14 eval()s int2vector column text sent by the server.

A minimal PostgreSQL wire-protocol server answers every query with one int2vector (OID 22)
column whose text is Python that would create a marker file. Importing the Redshift client
must leave the driver unable to run it.
"""

import contextlib
import os
import socket
import struct
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

redshift_connector = pytest.importorskip("redshift_connector")

from redshift_connector.utils import redshift_types, type_utils  # noqa: E402
from redshift_connector.utils.oids import RedshiftOID  # noqa: E402

from app.sources.client.redshift import redshift as redshift_client_module  # noqa: E402

INT2VECTOR_OID = 22


def _msg(code: bytes, body: bytes) -> bytes:
    return code + struct.pack("!i", len(body) + 4) + body


def _ready() -> bytes:
    return _msg(b"Z", b"I")


def _row_description() -> bytes:
    field = b"v\x00" + struct.pack("!ihihih", 0, 0, INT2VECTOR_OID, -1, -1, 0)
    return _msg(b"T", struct.pack("!h", 1) + field)


def _data_row(text: bytes) -> bytes:
    return _msg(b"D", struct.pack("!h", 1) + struct.pack("!i", len(text)) + text)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _serve(listener: socket.socket, payload: bytes) -> None:
    conn, _ = listener.accept()
    with conn:
        try:
            length = struct.unpack("!i", _recv_exact(conn, 4))[0]
            _recv_exact(conn, length - 4)
            conn.sendall(_msg(b"R", struct.pack("!i", 0)) + _ready())
            syncs = 0
            while True:
                code = _recv_exact(conn, 1)
                length = struct.unpack("!i", _recv_exact(conn, 4))[0]
                _recv_exact(conn, length - 4)
                if code == b"X":
                    return
                if code != b"S":
                    continue
                syncs += 1
                if syncs == 1:  # Parse + Describe + Sync
                    conn.sendall(_msg(b"1", b"") + _msg(b"t", struct.pack("!h", 0)) + _row_description() + _ready())
                else:  # Bind + Execute + Sync
                    conn.sendall(
                        _msg(b"2", b"") + _data_row(payload) + _msg(b"C", b"SELECT 1\x00") + _ready()
                    )
        except (EOFError, OSError):
            return


@pytest.fixture
def rogue_server(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    marker = tmp_path / "pwned"
    # The vulnerable decoder turns spaces into commas, so the payload has none.
    payload = f"__import__('pathlib').Path('{marker}').touch()".encode()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    thread = threading.Thread(target=_serve, args=(listener, payload), daemon=True)
    thread.start()
    yield listener.getsockname()[1], marker
    listener.close()
    thread.join(timeout=5)


def test_query_against_rogue_server_does_not_execute_server_supplied_code(rogue_server: tuple[int, Path]) -> None:
    port, marker = rogue_server
    conn = redshift_connector.connect(
        host="127.0.0.1", port=port, database="dev", user="u", password="p", ssl=False, timeout=5,
    )
    try:
        cursor = conn.cursor()
        with pytest.raises(ValueError):
            cursor.execute("select 1")
            cursor.fetchall()
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    assert not Path(marker).exists(), "redshift_connector evaluated a server-supplied column value"


def test_vector_in_does_not_eval() -> None:
    data = b"__import__('os').getpid()"
    with pytest.raises(ValueError):
        type_utils.vector_in(data, 0, len(data))
    decoder = redshift_types[RedshiftOID.SMALLINT_VECTOR][1]
    with pytest.raises(ValueError):
        decoder(data, 0, len(data))


def test_int2vector_still_decodes() -> None:
    decoder = redshift_types[RedshiftOID.SMALLINT_VECTOR][1]
    assert decoder(b"1 2 3", 0, 5) == [1, 2, 3]
    assert decoder(b"xx 4 -5 xx", 3, 4) == [4, -5]
    assert decoder(b"", 0, 0) == []


def test_patch_is_a_no_op_on_a_fixed_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    original_decoder = object()
    monkeypatch.setattr(redshift_connector, "__version__", "2.1.14")
    monkeypatch.setattr(type_utils, "vector_in", original_decoder)
    monkeypatch.setitem(redshift_types, RedshiftOID.SMALLINT_VECTOR, (0, original_decoder))

    redshift_client_module._patch_int2vector_decoder()

    assert type_utils.vector_in is original_decoder
    assert redshift_types[RedshiftOID.SMALLINT_VECTOR] == (0, original_decoder)


def test_patch_replaces_the_decoder_on_a_vulnerable_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redshift_connector, "__version__", "2.1.13")
    monkeypatch.setattr(type_utils, "vector_in", os.getpid)
    monkeypatch.setitem(redshift_types, RedshiftOID.SMALLINT_VECTOR, (0, os.getpid))

    redshift_client_module._patch_int2vector_decoder()

    assert type_utils.vector_in is redshift_client_module._int2vector_in
    assert redshift_types[RedshiftOID.SMALLINT_VECTOR] == (0, redshift_client_module._int2vector_in)
