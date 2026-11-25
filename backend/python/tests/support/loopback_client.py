"""The HTTP client a test uses against a server it runs itself (an aiohttp ``TestServer``, a served app on loopback).

httpx's default is a 5 s timeout on every phase, which turns a wire-shape test into a wall-clock assertion on the
test process (GW6-FU-1). The client and the served app share one event loop and one thread, so when that process
is not scheduled (a gc pass over a large xdist worker, a CPU-starved gate host) neither runs: in gate
``e6be6eca11`` the gateway served the Gemini stream in 3 ms, but only once the process ran again, after the
client's 5 s had already passed (its log: ``reasoning_dropped`` at .207, ``client_gone`` at .210). The bound here
still names a real hang, as httpx's own error and inside pytest-timeout's 30 s per test, without timing the host.
"""

from __future__ import annotations

from typing import Any

import httpx

LOOPBACK_TIMEOUT = httpx.Timeout(20.0)


def loopback_client(**kwargs: Any) -> httpx.AsyncClient:  # noqa: ANN401 - httpx.AsyncClient's keyword arguments
    kwargs.setdefault("timeout", LOOPBACK_TIMEOUT)
    return httpx.AsyncClient(**kwargs)
