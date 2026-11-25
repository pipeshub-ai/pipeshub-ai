"""The e2e lanes' MCP servers (``deployment/e2e/fake_saas/kinds.py``) on a loopback socket, for unit tests.

A real MCP server process-in-a-thread: a test that needs a server that refuses a credential, or an OAuth provider
whose tokens expire, connects to it with the product's own client instead of a stub of the client.
"""

from __future__ import annotations

import contextlib
import importlib.util
import ipaddress
import os
import socket
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import uvicorn

from app.agents.mcp import client as client_module
from app.agents.mcp import url_guard
from app.utils.url_fetcher import PublicTarget

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVER = Path(__file__).resolve().parents[6] / "deployment" / "e2e" / "fake_saas" / "kinds.py"
CONTROL = {"X-E2E-Control": "control"}


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ["FAKE_SAAS_CONTROL_TOKEN"] = CONTROL["X-E2E-Control"]
    spec = importlib.util.spec_from_file_location("mcpe2e_fake_kinds", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kinds = _load()


@contextlib.contextmanager
def serving() -> Iterator[str]:
    """The servers' base URL while the body runs."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(kinds.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    ready = threading.Event()
    for _ in range(200):
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                break
        _ = ready.wait(0.05)
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextlib.contextmanager
def loopback_allowed() -> Iterator[None]:
    """Main's URL guard refuses loopback; these servers are on it, so its one resolution step approves 127.0.0.1."""

    async def approve(url: str, *, allow_private: bool) -> PublicTarget:
        parts = urlsplit(url)
        return PublicTarget(parts.scheme, parts.hostname or "", parts.port or 80, (ipaddress.ip_address("127.0.0.1"),))

    with patch.object(url_guard, "assert_mcp_url_allowed", approve), \
         patch.object(client_module, "check_mcp_url", new=AsyncMock()):
        yield


def make(base: str, server_id: str, **spec: Any) -> dict[str, Any]:  # noqa: ANN401
    tools = spec.pop("tools", {"whoami": {"description": "Who the server sees.", "readOnly": True}})
    answer = httpx.put(f"{base}/__control/servers/{server_id}", json={"tools": tools, **spec}, headers=CONTROL)
    assert answer.status_code == 200, answer.text
    return answer.json()


def view(base: str, server_id: str) -> dict[str, Any]:
    return httpx.get(f"{base}/__control/servers/{server_id}", headers=CONTROL).json()


__all__ = ["CONTROL", "kinds", "loopback_allowed", "make", "serving", "view"]
