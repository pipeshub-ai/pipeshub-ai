"""A free loopback TCP port, asked of the kernel (bind to port 0), never a number chosen in advance."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.telemetry.ops_exporter import OpsExporter


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def bind_on_free_port(build: Callable[[int], OpsExporter], attempts: int = 5) -> OpsExporter:
    """``build(port)`` an exporter on a kernel-chosen port and bind it, trying a new port only when the bind
    failed because someone else took the port in between; any other failure is raised at once."""
    for _ in range(attempts):
        port = free_port()
        exporter = build(port)
        if exporter.try_bind():
            return exporter
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                raise AssertionError(f"the exporter could not bind port {port}, and nothing else holds it")
    raise AssertionError(f"every one of {attempts} free ports was taken before the exporter could bind it")
