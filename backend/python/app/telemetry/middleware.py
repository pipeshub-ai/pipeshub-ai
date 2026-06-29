"""ASGI middleware that instruments every HTTP request with telemetry metrics.

Registered once per FastAPI app (see ``setup_telemetry``), this makes collection
*default-on*: new routes are covered automatically, fixing the per-route opt-in
drift that left the Python services entirely uninstrumented.
"""

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.telemetry.identity import domain_from_email
from app.telemetry.modules.http_metrics import HTTP_REQUESTS, HTTP_REQUEST_DURATION

# Collapses id-like path segments so a fallback (no matched route template) still
# stays low-cardinality: mongo ObjectIds, uuids, long hex, and pure numbers.
_ID_SEGMENT = re.compile(
    r"^(?:[0-9a-fA-F]{24}|[0-9a-fA-F-]{32,36}|\d+)$"
)


def _normalize_path(path: str) -> str:
    parts = [(":id" if _ID_SEGMENT.match(seg) else seg) for seg in path.split("/")]
    return "/".join(parts) or "/"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request count + latency for every request handled by the app."""

    def __init__(self, app: ASGIApp, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = self._route_template(request)
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
    def _route_template(request: Request) -> str:
        """Matched route template (low cardinality), with a safe fallback."""
        route = request.scope.get("route")
        template = getattr(route, "path_format", None) or getattr(route, "path", None)
        if template:
            return template
        # No route matched (404) or older Starlette — normalize the raw path.
        return _normalize_path(request.url.path)

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
