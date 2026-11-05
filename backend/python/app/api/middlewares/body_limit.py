"""Reject oversized request bodies with 413 before they are buffered.

Raw ASGI so the check runs ahead of FastAPI parsing the body into memory. A
declared ``Content-Length`` is refused up front; a body without one (chunked)
is counted as it streams in.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodyLimitMiddleware:
    """``limits`` maps an exact request path to its maximum body size in bytes."""

    def __init__(self, app: ASGIApp, limits: Mapping[str, int]) -> None:
        self.app = app
        self.limits = dict(limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self.limits.get(scope.get("path", "")) if scope.get("type") == "http" else None
        if limit is None or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        for key, value in scope.get("headers") or []:
            if key.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await self._reject(send, limit)
                    return
                break

        seen = 0
        started = False
        exceeded = False

        async def counting_receive() -> dict[str, Any]:
            nonlocal seen, exceeded
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # The framework turns an exception raised here into a 400,
                    # so look like a client that went away and answer 413 below.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal started
            if exceeded:
                return  # the app's reaction to the fake disconnect (a 400) must not win
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            {"detail": f"Request body is too large (limit {limit} bytes)."}
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
