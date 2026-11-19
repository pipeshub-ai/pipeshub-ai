"""The route template a request is for, for metrics labels and log fields: bounded, never the raw path."""

from collections.abc import Mapping

from starlette.routing import Match

UNMATCHED = "unmatched"


def _template(route: object) -> str | None:
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) else None


def route_template(scope: Mapping[str, object]) -> str:
    """The matched route's template; for a request refused before routing (an auth middleware's 401), the route it
    would have reached, chosen as Starlette's router does (first FULL match, else first PARTIAL); else ``unmatched``."""
    matched = _template(scope.get("route"))
    if matched:
        return matched
    router = getattr(scope.get("app"), "router", None)
    partial = None
    for route in getattr(router, "routes", ()):
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return _template(route) or UNMATCHED
        if match is Match.PARTIAL and partial is None:
            partial = route
    return (_template(partial) if partial is not None else None) or UNMATCHED
