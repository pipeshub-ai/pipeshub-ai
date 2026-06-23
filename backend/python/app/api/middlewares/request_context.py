"""Per-request trace context middleware.

Raw ASGI (not ``BaseHTTPMiddleware``) so the contextvar set here propagates into
the endpoint — ``BaseHTTPMiddleware`` runs the app in a separate task and breaks
that. Register it outermost so even auth-middleware logs carry the id.
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.utils.request_context import (
    HEADER_REQUEST_ID,
    new_anon_root,
    reset_context,
    sanitize_root_id,
    set_context,
)

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _header(headers: List[Tuple[bytes, bytes]], name: str) -> Optional[str]:
    target = name.encode("latin-1").lower()
    for key, value in headers:
        if key.lower() == target:
            return value.decode("latin-1")
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers: List[Tuple[bytes, bytes]] = scope.get("headers") or []
        root_id = sanitize_root_id(_header(headers, HEADER_REQUEST_ID)) or new_anon_root()

        token = set_context(root_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_context(token)
