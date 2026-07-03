"""Tests for app.api.middlewares.request_context (the ASGI wiring).

These assert the entry point — not just sanitize_root_id() in isolation — so a
refactor that drops the sanitizer call or mis-wires the contextvar is caught.
"""

import pytest

from app.api.middlewares.request_context import RequestContextMiddleware
from app.utils import request_context as rc


def _http_scope(headers=None):
    return {
        "type": "http",
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in (headers or [])],
    }


async def _noop_receive():
    return {"type": "http.request"}


async def _noop_send(_message):
    return None


async def _run(headers):
    """Drive the middleware and return the root_id bound while the app ran."""
    captured = {}

    async def app(scope, receive, send):
        ctx = rc.get_context()
        captured["root_id"] = ctx.root_id if ctx else None

    await RequestContextMiddleware(app)(_http_scope(headers), _noop_receive, _noop_send)
    return captured["root_id"]


class TestRequestContextMiddleware:
    @pytest.mark.asyncio
    async def test_forged_request_id_is_sanitized_in_context(self):
        # CR/LF + fake log line must be stripped before it ever reaches a log.
        root_id = await _run([("x-request-id", "abc\r\nFAKE 500 error")])
        assert root_id == "abcFAKE500error"
        assert "\r" not in root_id and "\n" not in root_id

    @pytest.mark.asyncio
    async def test_clean_request_id_is_preserved(self):
        root_id = await _run([("x-request-id", "6a3992e0a771842adbf1039f-ZgUvzvsipDj0C_kjKwhMj")])
        assert root_id == "6a3992e0a771842adbf1039f-ZgUvzvsipDj0C_kjKwhMj"

    @pytest.mark.asyncio
    async def test_oversized_request_id_is_capped(self):
        root_id = await _run([("x-request-id", "a" * 500)])
        assert root_id == "a" * rc._MAX_ROOT_ID_LEN

    @pytest.mark.asyncio
    async def test_missing_request_id_falls_back_to_anon_root(self):
        root_id = await _run([])
        assert root_id is not None and root_id != ""

    @pytest.mark.asyncio
    async def test_context_is_reset_after_request(self):
        await _run([("x-request-id", "clean-id")])
        assert rc.get_context() is None
