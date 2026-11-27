"""The runner's bridge: a TCP port on loopback to a STDIO MCP server, one server process per connection.

Seeded read-only into the runner and run as ``python3 bridge.py <port> -- <command> [args...]``.
It imports only the standard library: it runs in whatever image holds the server. The
environment it is started with (a placeholder for each of the server's secrets, which the
egress proxy swaps on the way out) is the environment of every server process it spawns;
it never prints or logs it. When that environment carries the proxy's trust step
(``PXH_EGRESS_TRUST``) the bridge runs it first, so the server trusts the certificate the
proxy ends its TLS with, and does not start without it.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading
from typing import IO

READY = b"PXH-MCP-BRIDGE-READY\n"
TRUST_ENV = "PXH_EGRESS_TRUST"
_CHUNK = 65536
_EXIT_GRACE_S = 5.0


def _pump_in(conn: socket.socket, stdin: IO[bytes] | None) -> None:
    if stdin is None:
        return
    with contextlib.suppress(OSError):
        while data := conn.recv(_CHUNK):
            _ = stdin.write(data)
            stdin.flush()
    with contextlib.suppress(OSError):
        stdin.close()


def _serve(conn: socket.socket, command: list[str]) -> None:
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)
    except OSError as exc:
        sys.stderr.write(f"pxh-mcp-bridge: cannot start the server ({type(exc).__name__})\n")
        sys.stderr.flush()
        conn.close()
        return
    inbound = threading.Thread(target=_pump_in, args=(conn, process.stdin), daemon=True)
    inbound.start()
    stdout = process.stdout
    with contextlib.suppress(OSError):
        while stdout is not None and (data := os.read(stdout.fileno(), _CHUNK)):
            conn.sendall(data)
    with contextlib.suppress(OSError):
        conn.shutdown(socket.SHUT_RDWR)
    conn.close()
    try:
        _ = process.wait(timeout=_EXIT_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()
        _ = process.wait()


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("usage: bridge.py <port> -- <command> [args...]\n")
        return 2
    port, command = int(argv[1]), argv[3:]
    trust = os.environ.pop(TRUST_ENV, None)
    if trust:
        try:
            exec(compile(trust, "pxh-egress-trust", "exec"), {"__name__": "pxh_egress_trust"})
        except BaseException as exc:
            sys.stderr.write(f"pxh-mcp-bridge: cannot trust the egress proxy ({type(exc).__name__}: {exc})\n")
            sys.stderr.flush()
            return 3
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(16)
    sys.stdout.buffer.write(READY)
    sys.stdout.buffer.flush()
    while True:
        conn, _ = server.accept()
        threading.Thread(target=_serve, args=(conn, command), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
