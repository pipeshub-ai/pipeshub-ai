"""A free loopback TCP port, asked of the kernel (bind to port 0), never a number chosen in advance."""
from __future__ import annotations

import socket


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]
