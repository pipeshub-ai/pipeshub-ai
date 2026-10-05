"""Client, constants and helpers for the strict OpenAPI audit of /mcp."""

from __future__ import annotations

from typing import Any

import requests

from helper.http.api_client import APIClient

# Mounted on the app root, not under /api/v1.
MCP_BASE = "/mcp"
SSE_MEDIA_TYPE = "text/event-stream"
JSON_MEDIA_TYPE = "application/json"
PROTOCOL_VERSION_HEADER = "Mcp-Protocol-Version"
UNSUPPORTED_PROTOCOL_VERSION = "1999-01-01"
MALFORMED_TOKEN = "not-a-jwt"
STREAM_WINDOW_SECONDS = 5.0
_CONNECT_TIMEOUT_SECONDS = 30.0


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def freeze_stream(resp: requests.Response) -> requests.Response:
    """Read a ``stream=True`` response until it ends or its read timeout fires, then close it.

    The standalone SSE stream never ends, so ``resp.content`` (which the strict
    checker reads) would otherwise raise or block. Afterwards ``resp.content``
    holds whatever arrived before the stream went idle, usually ``b""``.
    """
    chunks: list[bytes] = []
    try:
        for chunk in resp.iter_content(chunk_size=None):
            chunks.append(chunk)
    except requests.RequestException:
        pass
    finally:
        resp.close()
    # requests has no public way to mark a partially read body as final.
    resp._content = b"".join(chunks)
    resp._content_consumed = True
    return resp


class McpEndpointClient(APIClient):
    """Client for /mcp, acting as the shared org admin unless ``auth=False``."""

    BASE = MCP_BASE

    def get_root(
        self,
        *,
        auth: bool = True,
        accept: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """GET /mcp with a complete body. Do not pass an SSE ``accept`` here: use ``open_stream``."""
        merged = dict(headers or {})
        if accept is not None:
            merged["Accept"] = accept
        return self.get("", auth=auth, headers=merged, **kwargs)

    def open_stream(
        self,
        *,
        auth: bool = True,
        headers: dict[str, str] | None = None,
        window: float = STREAM_WINDOW_SECONDS,
    ) -> requests.Response:
        """GET /mcp accepting SSE; safe whether the server streams or answers with an error.

        ``window`` is the read timeout: headers must arrive within it, and an
        open stream is cut after that many idle seconds.
        """
        resp = self.get(
            "",
            auth=auth,
            headers={"Accept": SSE_MEDIA_TYPE, **(headers or {})},
            stream=True,
            timeout=(_CONNECT_TIMEOUT_SECONDS, window),
        )
        return freeze_stream(resp)
