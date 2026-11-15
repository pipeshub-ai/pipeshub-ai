"""ASGI middleware that instruments every HTTP request with telemetry metrics.

Registered once per FastAPI app (see ``setup_telemetry``), this makes collection
*default-on*: new routes are covered automatically, fixing the per-route opt-in
drift that left the Python services entirely uninstrumented.
"""

import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.telemetry.identity import domain_from_email
from app.telemetry.modules.http_metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    bound_route,
)
from app.utils.route_template import route_template


class MetricsMiddleware:
    """Records request count + latency for every request handled by the app.

    Raw ASGI: behind a ``BaseHTTPMiddleware`` a route's ``Request.is_disconnected()`` always answers False.
    """

    def __init__(self, app: ASGIApp, service_name: str) -> None:
        self.app = app
        self.service_name = service_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        elapsed: float | None = None
        status_code = 500

        async def send_and_record_status(message: Message) -> None:
            nonlocal elapsed, status_code
            if message["type"] == "http.response.start":
                # Latency to the response's start, not to its last byte: a stream's length is not the route's latency.
                elapsed = time.perf_counter() - start
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_record_status)
        finally:
            if elapsed is None:
                elapsed = time.perf_counter() - start
            request = Request(scope)
            route = bound_route(route_template(scope))
            org = self._org(request)
            domain = self._domain(request)
            # Never reset counters — push cumulative values, let the TSDB
            # compute rate()/increase().
            HTTP_REQUESTS.inc(
                self.service_name, route, request.method, str(status_code), org, domain
            )
            HTTP_REQUEST_DURATION.observe(
                self.service_name, route, request.method, value=elapsed
            )

    @staticmethod
    def _org(request: Request) -> str:
        """Org id from the authenticated request state, if present.

        Auth middleware attaches the JWT payload to ``request.state.user``.
        org is bounded per install, so it is safe as a metric label.
        """
        user = getattr(request.state, "user", None)
        if isinstance(user, dict):
            org = user.get("orgId") or user.get("org_id")
            if org:
                return str(org)
        return "unknown"

    @staticmethod
    def _domain(request: Request) -> str:
        """Org email-domain from the authenticated request state, if present.

        Lets dashboards exclude internal (PipesHub) traffic. Derived from the JWT
        email attached to ``request.state.user`` by the auth middleware.
        """
        user = getattr(request.state, "user", None)
        if isinstance(user, dict):
            return domain_from_email(user.get("email"))
        return "unknown"
