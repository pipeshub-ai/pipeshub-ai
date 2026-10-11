"""The MCP SDK version the client depends on.

On the 2.x line, `mcp` 2.2.0 fixes client advisories this code relies on: GHSA-rwrf-2pqf-9j8j
(output-schema `$ref`s fetched from server-chosen URLs, outside our URL guard), GHSA-5h93-6whr-6q8j
(headers re-sent on cross-origin redirects) and GHSA-qx49-fqc8-xw99 (OAuth credentials sent to a
server-chosen authorization server). The client is written against the 2.x API.
"""
from __future__ import annotations

from importlib.metadata import version

from packaging.version import Version


def test_the_installed_sdk_is_2x_with_the_client_security_fixes() -> None:
    installed = Version(version("mcp"))
    assert Version("2.2.0") <= installed < Version("3.0.0")


def test_the_session_still_says_when_its_connection_ended() -> None:
    """`client._connection_closed` reads this private state; if it moves, a call after the
    connection ended is reported as interrupted instead of being sent on a new session."""
    import anyio
    from mcp.client.session import ClientSession

    from app.agents.mcp.client import _connection_closed

    send, receive = anyio.create_memory_object_stream(1)
    session = ClientSession(receive, send)
    assert session._dispatcher._closed is False
    session._dispatcher._closed = True

    class _Client:
        _session = session

    assert _connection_closed(_Client()) is True  # type: ignore[arg-type]

