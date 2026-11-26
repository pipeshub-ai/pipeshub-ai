"""GW6-FU-1: a test that serves an app itself talks to it through ``loopback_client``, never httpx's 5 s default.

httpx's default timeout made the Gemini stream test a wall-clock assertion on the gate host
(``tests/support/loopback_client.py`` has the evidence); any test that starts a server of its own and opens a bare
``httpx.AsyncClient()`` carries the same hidden bound.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.cohesion.support import PYTHON_ROOT

_SERVES = re.compile(r"\bTestServer\(|\.start_server\(|\bweb\.TCPSite\(|\buvicorn\.")
_BARE_CLIENT = re.compile(r"\bhttpx\.AsyncClient\(\)")


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted((PYTHON_ROOT / "tests").rglob("test_*.py")):
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8")
        if _SERVES.search(text) and _BARE_CLIENT.search(text):
            found.append(str(path.relative_to(PYTHON_ROOT)))
    return found


def test_gw6_fu_1_a_served_app_is_reached_without_a_wall_clock_bound() -> None:
    offenders = _offenders()
    assert not offenders, (
        f"{offenders} start a server and reach it with a bare httpx.AsyncClient(), whose 5 s default times the host, "
        "not the server: use tests.support.loopback_client.loopback_client()"
    )


def test_gw6_fu_1_the_rule_sees_a_bare_client() -> None:
    assert _SERVES.search("server = TestServer(app)") and _BARE_CLIENT.search("async with httpx.AsyncClient() as c:")
    assert not _BARE_CLIENT.search("httpx.AsyncClient(timeout=LOOPBACK_TIMEOUT)")
